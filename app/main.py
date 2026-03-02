import base64
import io
import json
import os
import uuid
from pathlib import Path
from typing import Optional

import numpy as np
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

Base.metadata.create_all(bind=engine, checkfirst=True)

app = FastAPI(title="Satellite Change Detection", version="1.0.0")

# Mount static files
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
OVERLAYS_DIR = DATA_DIR / "overlays"
OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)

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
    enable_registration: bool = Form(True),
    enable_normalization: bool = Form(True),
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
    # Save overlay to disk and store path (optional)
    overlay_filename = f"{user.id}_{uuid.uuid4().hex}.png"
    overlay_path = OVERLAYS_DIR / overlay_filename
    Image.fromarray(result_image).save(overlay_path)
    relative_overlay = f"overlays/{overlay_filename}"
    regions_serializable = [
        {
            "id": int(r["id"]),
            "area": int(r["area"]),
            "center": {"x": int(r["center"][0]), "y": int(r["center"][1])},
            "bbox": {"x": int(r["bbox"][0]), "y": int(r["bbox"][1]), "w": int(r["bbox"][2]), "h": int(r["bbox"][3])},
            "objectType": str(r["object_type"]),
            "confidence": float(r["confidence"]),
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
        total_pixels=total_px,
        changed_pixels=changed_px,
        change_percentage=change_pct,
        regions_count=len(change_regions),
        overlay_path=relative_overlay,
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
    return {
        "id": run.id,
        "title": run.title,
        "method": run.method,
        "statistics": {
            "totalPixels": total_px,
            "changedPixels": changed_px,
            "unchangedPixels": unchanged_px,
            "changePercentage": change_pct,
        },
        "regions": regions_serializable,
        "overlayBase64Png": overlay_b64,
        "overlayUrl": f"/api/overlay/{relative_overlay}",
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
            "changePercentage": r.change_percentage,
            "regionsCount": r.regions_count,
            "overlayUrl": f"/api/overlay/{r.overlay_path}" if r.overlay_path else None,
            "createdAt": r.created_at.isoformat(),
        }
        for r in runs
    ]


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
    # Delete overlay file if it exists
    if run.overlay_path:
        overlay_file = OVERLAYS_DIR.parent / run.overlay_path
        if overlay_file.exists():
            overlay_file.unlink(missing_ok=True)
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
