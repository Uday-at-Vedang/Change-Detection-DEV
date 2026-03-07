import base64
import io
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
from sqlalchemy import text as sa_text
from fastapi import FastAPI, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from PIL import Image

from .auth import (
    COOKIE_NAME,
    create_access_token,
    get_password_hash,
    get_user_by_email,
    get_current_user,
    get_user_from_token,
    verify_password,
)
from .database import Base, engine, get_db, DATA_DIR
from .models import User, DetectionRun
from .detection_engine import run_detection
from .notifier import send_notification

Base.metadata.create_all(bind=engine, checkfirst=True)

# Lightweight migration: add columns introduced after initial schema
with engine.connect() as conn:
    for col, col_type in [
        ("zone", "VARCHAR(128) DEFAULT ''"),
        ("village", "VARCHAR(128) DEFAULT ''"),
        ("before_thumb_path", "VARCHAR(512) DEFAULT ''"),
        ("after_thumb_path", "VARCHAR(512) DEFAULT ''"),
    ]:
        try:
            conn.execute(sa_text(
                f"ALTER TABLE detection_runs ADD COLUMN {col} {col_type}"))
            conn.commit()
        except Exception:
            conn.rollback()

app = FastAPI(title="AI Change Detection", version="2.0.0")

# Mount static files
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
OVERLAYS_DIR = DATA_DIR / "overlays"
OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
THUMB_MAX_SIZE = 200  # max width or height for history thumbnails

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- Schemas ---
class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str = ""


class UserLogin(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str


# --- Auth routes ---
def _auth_response(token: str, user: User):
    """JSON response with auth cookie so browser sends token on every request (e.g. POST /api/detect)."""
    payload = {"access_token": token, "token_type": "bearer", "user": {"id": user.id, "email": user.email, "full_name": user.full_name}}
    response = JSONResponse(content=payload)
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=60 * 60 * 24 * 7,  # 7 days
        httponly=True,
        samesite="lax",
        path="/",
    )
    return response


@app.post("/api/auth/register")
def register(data: UserCreate, db: Session = Depends(get_db)):
    try:
        if get_user_by_email(db, data.email):
            raise HTTPException(status_code=400, detail="Email already registered")
        hashed = get_password_hash(data.password)
        user = User(
            email=data.email,
            hashed_password=hashed,
            full_name=data.full_name,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        token = create_access_token(data={"sub": str(user.id)})
        return _auth_response(token, user)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[REGISTER] Error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Registration failed: {type(e).__name__}: {e}")


@app.post("/api/auth/login")
def login(data: UserLogin, db: Session = Depends(get_db)):
    try:
        user = get_user_by_email(db, data.email)
        if not user or not verify_password(data.password, user.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")
        token = create_access_token(data={"sub": str(user.id)})
        return _auth_response(token, user)
    except HTTPException:
        raise
    except Exception as e:
        print(f"[LOGIN] Error: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Login failed: {type(e).__name__}: {e}")


@app.post("/api/auth/logout")
def logout():
    """Clear auth cookie so subsequent requests are unauthenticated."""
    response = JSONResponse(content={"ok": True})
    response.delete_cookie(COOKIE_NAME, path="/")
    return response


class PasswordReset(BaseModel):
    email: str
    new_password: str


@app.post("/api/auth/reset-password")
def reset_password(data: PasswordReset, db: Session = Depends(get_db)):
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    user = get_user_by_email(db, data.email)
    if not user:
        # Intentionally vague to prevent email enumeration
        raise HTTPException(status_code=404, detail="No account found with that email")
    user.hashed_password = get_password_hash(data.new_password)
    db.commit()
    return {"ok": True, "message": "Password has been reset. You can now sign in."}


# NOTE: This reset flow has no email verification. In production, implement
# a token-based flow: POST /forgot sends email with one-time link,
# GET /reset?token=... validates token, POST /reset sets new password.


@app.get("/api/me")
def me(user: Optional[User] = Depends(get_current_user)):
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"id": user.id, "email": user.email, "full_name": user.full_name}


# --- Detection route ---
@app.post("/api/detect")
async def detect(
    request: Request,
    before: UploadFile = File(...),
    after: UploadFile = File(...),
    method: str = Form("AI-Based Deep Learning"),
    title: str = Form("Untitled run"),
    zone: str = Form(""),
    village: str = Form(""),
    enable_registration: bool = Form(True),
    enable_normalization: bool = Form(True),
    notify_email: Optional[str] = Form(None),
    access_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    # Resolve user from token (header, cookie, or form - in case browser strips headers for multipart)
    token = None
    auth_header = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()
    if not token:
        token = request.cookies.get(COOKIE_NAME)
    if not token:
        token = access_token
    user = get_user_from_token(token, db) if token else None
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
    try:
        before_bytes = await before.read()
        after_bytes = await after.read()
        if len(before_bytes) > MAX_UPLOAD_BYTES or len(after_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=400, detail="Image too large (max 20 MB)")
        before_pil = Image.open(io.BytesIO(before_bytes)).convert("RGB")
        after_pil = Image.open(io.BytesIO(after_bytes)).convert("RGB")
        del before_bytes, after_bytes
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid image: {e}")
    change_mask, result_image, stats, change_regions = run_detection(
        before_pil, after_pil, method=method, enable_registration=enable_registration, enable_normalization=enable_normalization
    )
    # Save overlay and thumbnails for history table view
    base_name = f"{user.id}_{uuid.uuid4().hex}"
    overlay_filename = base_name + ".png"
    overlay_path = OVERLAYS_DIR / overlay_filename
    Image.fromarray(result_image).save(overlay_path)
    relative_overlay = f"overlays/{overlay_filename}"

    # Save before/after thumbnails for history table (efficient small images)
    before_thumb_file = OVERLAYS_DIR / f"{base_name}_before_thumb.png"
    after_thumb_file = OVERLAYS_DIR / f"{base_name}_after_thumb.png"
    before_thumb_pil = before_pil.copy()
    before_thumb_pil.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.Resampling.LANCZOS)
    before_thumb_pil.save(before_thumb_file)
    after_thumb_pil = after_pil.copy()
    after_thumb_pil.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.Resampling.LANCZOS)
    after_thumb_pil.save(after_thumb_file)
    relative_before_thumb = f"overlays/{base_name}_before_thumb.png"
    relative_after_thumb = f"overlays/{base_name}_after_thumb.png"

    regions_serializable = [
        {
            "id": int(r["id"]),
            "area": int(r["area"]),
            "center": {"x": int(r["center"][0]), "y": int(r["center"][1])},
            "bbox": {"x": int(r["bbox"][0]), "y": int(r["bbox"][1]), "w": int(r["bbox"][2]), "h": int(r["bbox"][3])},
            "objectType": str(r["object_type"]),
            "confidence": float(r["confidence"]),
            "severity": r.get("severity", "minor"),
            "subType": r.get("sub_type"),
            "subTypeConfidence": float(r["sub_type_confidence"]) if r.get("sub_type_confidence") is not None else None,
            "estimatedStories": r.get("estimated_stories"),
            "estimatedHeightM": float(r["estimated_height_m"]) if r.get("estimated_height_m") is not None else None,
            "constructionStage": r.get("construction_stage"),
        }
        for r in change_regions
    ]
    total_px = int(stats["total_pixels"])
    changed_px = int(stats["changed_pixels"])
    unchanged_px = int(stats["unchanged_pixels"])
    change_pct = float(stats["change_percentage"])
    run = DetectionRun(
        user_id=user.id,
        title=title,
        method=method,
        zone=zone,
        village=village,
        total_pixels=total_px,
        changed_pixels=changed_px,
        change_percentage=change_pct,
        regions_count=len(change_regions),
        overlay_path=relative_overlay,
        before_thumb_path=relative_before_thumb,
        after_thumb_path=relative_after_thumb,
        regions_json=json.dumps(regions_serializable),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    # Base64 overlay for immediate display
    buf = io.BytesIO()
    Image.fromarray(result_image).save(buf, format="PNG")
    buf.seek(0)
    overlay_b64 = base64.b64encode(buf.read()).decode("utf-8")

    # Send email notification if requested
    notification_sent = False
    if notify_email and notify_email.strip():
        notification_sent = send_notification(
            recipient=notify_email.strip(),
            title=title,
            method=method,
            zone=zone,
            village=village,
            change_pct=change_pct,
            changed_px=changed_px,
            total_px=total_px,
            regions=regions_serializable,
        )

    return {
        "id": run.id,
        "title": run.title,
        "method": run.method,
        "zone": run.zone or "",
        "village": run.village or "",
        "statistics": {
            "totalPixels": total_px,
            "changedPixels": changed_px,
            "unchangedPixels": unchanged_px,
            "changePercentage": change_pct,
        },
        "regions": regions_serializable,
        "overlayBase64Png": overlay_b64,
        "overlayUrl": f"/api/overlay/{relative_overlay}",
        "beforeThumbUrl": f"/api/overlay/{relative_before_thumb}",
        "afterThumbUrl": f"/api/overlay/{relative_after_thumb}",
        "notificationSent": notification_sent,
        "createdAt": run.created_at.isoformat(),
    }


@app.get("/api/overlay/{path:path}")
def serve_overlay(path: str):
    # Restrict to overlays directory
    full = (OVERLAYS_DIR.parent / path).resolve()
    base = OVERLAYS_DIR.parent.resolve()
    try:
        full.relative_to(base)
    except ValueError:
        raise HTTPException(404)
    if not full.exists() or not full.is_file():
        raise HTTPException(404)
    return FileResponse(full, media_type="image/png")


# --- History ---
@app.get("/api/history")
def history(
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    runs = db.query(DetectionRun).filter(DetectionRun.user_id == user.id).order_by(DetectionRun.created_at.desc()).limit(100).all()
    return [
        {
            "id": r.id,
            "title": r.title,
            "method": r.method,
            "zone": r.zone or "",
            "village": r.village or "",
            "changePercentage": r.change_percentage,
            "regionsCount": r.regions_count,
            "totalPixels": r.total_pixels,
            "changedPixels": r.changed_pixels,
            "overlayUrl": f"/api/overlay/{r.overlay_path}" if r.overlay_path else None,
            "beforeThumbUrl": f"/api/overlay/{r.before_thumb_path}" if (getattr(r, "before_thumb_path", None) or "").strip() else None,
            "afterThumbUrl": f"/api/overlay/{r.after_thumb_path}" if (getattr(r, "after_thumb_path", None) or "").strip() else None,
            "createdAt": r.created_at.isoformat(),
        }
        for r in runs
    ]


@app.get("/api/history/{run_id}")
def get_run(
    run_id: int,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Fetch a single run by id for opening from history (result view with slider, table, zoom)."""
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id, DetectionRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    regions = json.loads(run.regions_json) if run.regions_json else []
    return {
        "id": run.id,
        "title": run.title,
        "method": run.method,
        "zone": run.zone or "",
        "village": run.village or "",
        "statistics": {
            "totalPixels": run.total_pixels,
            "changedPixels": run.changed_pixels,
            "unchangedPixels": run.total_pixels - run.changed_pixels,
            "changePercentage": run.change_percentage,
        },
        "regions": regions,
        "overlayUrl": f"/api/overlay/{run.overlay_path}" if run.overlay_path else None,
        "beforeThumbUrl": f"/api/overlay/{run.before_thumb_path}" if (getattr(run, "before_thumb_path", None) or "").strip() else None,
        "afterThumbUrl": f"/api/overlay/{run.after_thumb_path}" if (getattr(run, "after_thumb_path", None) or "").strip() else None,
        "createdAt": run.created_at.isoformat(),
    }


# --- Delete history run ---
@app.delete("/api/history/{run_id}")
def delete_run(
    run_id: int,
    user: Optional[User] = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        raise HTTPException(status_code=401, detail="Login required")
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id, DetectionRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Delete overlay and thumbnail files if they exist
    for path_attr in ("overlay_path", "before_thumb_path", "after_thumb_path"):
        path_val = getattr(run, path_attr, None)
        if path_val:
            f = OVERLAYS_DIR.parent / path_val
            if f.exists():
                f.unlink(missing_ok=True)
    db.delete(run)
    db.commit()
    return {"ok": True, "deleted_id": run_id}


# --- Serve SPA ---
@app.get("/", response_class=HTMLResponse)
def index():
    index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Satellite Change Detection</h1><p>Create <code>templates/index.html</code> and <code>static/</code>.</p>")
    return FileResponse(index_file)
