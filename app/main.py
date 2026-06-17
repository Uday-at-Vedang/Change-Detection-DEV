import base64
import io
import json
import os
import uuid
from datetime import timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import text as sa_text
from fastapi import FastAPI, Depends, File, Form, HTTPException, UploadFile
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
    get_or_create_guest_user,
    get_user_from_token,
    verify_password,
)
from .database import Base, engine, get_db, DATA_DIR
from .models import User, DetectionRun
from . import dda as _dda_pkg  # noqa: F401 — register DDA tables
from .dda.models import DdaZone, DdaVillage, ImageAsset, DetectionJob  # noqa: F401
from .dda.config import IS_DDA_MODE
from .dda.bootstrap import init_dda_database, setup_dda
from .notifier import send_notification, send_test_email

import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


logger.info("Importing AI Change Detection app module")

_IST = timezone(timedelta(hours=5, minutes=30))


def _isoformat_ist(dt):
    """Convert a UTC datetime to IST (GMT+5:30) and return ISO-8601 string."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()

# Create tables and run migrations without crashing the app (HF Spaces can restart if startup fails)
try:
    logger.info("Running database initialization")
    Base.metadata.create_all(bind=engine, checkfirst=True)
    with engine.connect() as conn:
        for col, col_type in [
            ("zone", "VARCHAR(128) DEFAULT ''"),
            ("village", "VARCHAR(128) DEFAULT ''"),
            ("before_full_path", "VARCHAR(512) DEFAULT ''"),
            ("before_thumb_path", "VARCHAR(512) DEFAULT ''"),
            ("after_thumb_path", "VARCHAR(512) DEFAULT ''"),
        ]:
            try:
                conn.execute(sa_text(
                    f"ALTER TABLE detection_runs ADD COLUMN {col} {col_type}"))
                conn.commit()
            except Exception:
                conn.rollback()
    logger.info("Database initialization complete")
    init_dda_database()
except Exception as e:
    import logging
    logging.getLogger("uvicorn.error").warning("Startup migration skipped: %s", e)

app = FastAPI(title="AI Change Detection", version="2.3.0-dda" if IS_DDA_MODE else "2.2.0")
setup_dda(app)


@app.get("/health")
def health():
    """Health check + AdaptFormer model status (HF Spaces + diagnostics)."""
    from datetime import datetime
    from .model_inference import get_model_status

    model = get_model_status()
    return {
        "status": "ok" if model.get("available") else "degraded",
        "version": "2.3.0-dda" if IS_DDA_MODE else "2.2.1",
        "appMode": "dda" if IS_DDA_MODE else "legacy",
        "spaceId": os.environ.get("SPACE_ID", ""),
        "server_time_ist": _isoformat_ist(datetime.now(timezone.utc)),
        "adaptFormer": model,
    }


@app.on_event("startup")
def log_startup():
    logger.info("FastAPI startup event completed")
    try:
        from .model_inference import preload_model
        preload_model()
    except Exception as exc:
        logger.warning("Model preload at startup failed: %s", exc)

# Mount static files
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
OVERLAYS_DIR = DATA_DIR / "overlays"
try:
    OVERLAYS_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
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


class EmailRequest(BaseModel):
    email: str


def _load_regions_json(raw_regions: Optional[str]):
    try:
        return json.loads(raw_regions) if raw_regions else []
    except (json.JSONDecodeError, TypeError):
        return []


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
        logger.exception("Registration failed")
        raise HTTPException(status_code=500, detail=f"Registration failed: {type(e).__name__}")


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
        logger.exception("Login failed")
        raise HTTPException(status_code=500, detail=f"Login failed: {type(e).__name__}")


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
    before: UploadFile = File(...),
    after: UploadFile = File(...),
    method: str = Form("AI-Based Deep Learning"),
    title: str = Form("Untitled run"),
    zone: str = Form(""),
    village: str = Form(""),
    enable_registration: bool = Form(True),
    enable_normalization: bool = Form(True),
    detection_sensitivity: float = Form(0.5),
    min_region_area: Optional[int] = Form(None),
    notify_email: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_or_create_guest_user(db)
    MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB

    def _read_upload(upload: UploadFile, field_name: str):
        raw = None
        try:
            raw = upload.file.read()
            if raw is None or len(raw) == 0:
                raise HTTPException(status_code=400, detail=f"{field_name} image is empty")
            if len(raw) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=400, detail="Image too large (max 20 MB)")
            return Image.open(io.BytesIO(raw)).convert("RGB")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid {field_name} image: {e}")
        finally:
            try:
                if raw is not None:
                    del raw
            except Exception:
                pass

    before_pil = _read_upload(before, "before")
    after_pil = _read_upload(after, "after")
    detection_sensitivity = max(0.0, min(1.0, float(detection_sensitivity)))
    if min_region_area is not None:
        min_region_area = int(max(50, min(10000, min_region_area)))

    from .detection_engine import run_detection
    change_mask, result_image, stats, change_regions = run_detection(
        before_pil,
        after_pil,
        method=method,
        enable_registration=enable_registration,
        enable_normalization=enable_normalization,
        detection_sensitivity=detection_sensitivity,
        min_region_area=min_region_area,
    )
    # Save overlay and thumbnails for history table view
    base_name = f"{user.id}_{uuid.uuid4().hex}"
    overlay_filename = base_name + ".png"
    overlay_path = OVERLAYS_DIR / overlay_filename
    try:
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    try:
        Image.fromarray(result_image).save(overlay_path)
    except Exception as exc:
        logger.error("Failed to save overlay: %s", exc)
        raise HTTPException(status_code=500, detail="Could not save result image")
    relative_overlay = f"overlays/{overlay_filename}"

    # Save full-resolution before image (used by the before/after slider from history)
    relative_before_full = ""
    relative_before_thumb = ""
    relative_after_thumb = ""
    try:
        before_full_file = OVERLAYS_DIR / f"{base_name}_before.png"
        before_pil.save(before_full_file)
        relative_before_full = f"overlays/{base_name}_before.png"

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
    except Exception as exc:
        logger.warning("Failed to save thumbnails: %s", exc)

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
        before_full_path=relative_before_full,
        before_thumb_path=relative_before_thumb,
        after_thumb_path=relative_after_thumb,
        regions_json=json.dumps(regions_serializable),
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    # Read already-saved overlay for base64 (avoids re-encoding the numpy array)
    overlay_b64 = base64.b64encode(overlay_path.read_bytes()).decode("utf-8")

    # Send email notification if requested
    notification_sent = False
    notification_error = None
    if notify_email and notify_email.strip():
        notification_sent, notification_error = send_notification(
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
            "thresholdDebug": stats.get("threshold_debug", {}),
            "params": stats.get("params", {}),
            "alignmentWarning": stats.get("alignment_warning"),
            "registrationOk": stats.get("params", {}).get("registration_ok"),
        },
        "regions": regions_serializable,
        "overlayBase64Png": overlay_b64,
        "overlayUrl": f"/api/overlay/{relative_overlay}",
        "beforeFullUrl": f"/api/overlay/{relative_before_full}",
        "beforeThumbUrl": f"/api/overlay/{relative_before_thumb}",
        "afterThumbUrl": f"/api/overlay/{relative_after_thumb}",
        "notificationSent": notification_sent,
        "notificationError": notification_error,
        "createdAt": _isoformat_ist(run.created_at),
    }


@app.post("/api/notify/test")
def notify_test(
    data: EmailRequest,
    db: Session = Depends(get_db),
):
    get_or_create_guest_user(db)
    sent, error = send_test_email(data.email.strip())
    if not sent:
        raise HTTPException(status_code=400, detail=error or "Failed to send test email")
    return {"ok": True, "message": f"Test email sent to {data.email.strip()}."}


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
    db: Session = Depends(get_db),
):
    user = get_or_create_guest_user(db)
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
            "createdAt": _isoformat_ist(r.created_at),
        }
        for r in runs
    ]


@app.get("/api/history/{run_id}")
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
):
    """Fetch a single run by id for opening from history (result view with slider, table, zoom)."""
    user = get_or_create_guest_user(db)
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id, DetectionRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    regions = _load_regions_json(run.regions_json)
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
        "beforeFullUrl": f"/api/overlay/{run.before_full_path}" if (getattr(run, "before_full_path", None) or "").strip() else None,
        "beforeThumbUrl": f"/api/overlay/{run.before_thumb_path}" if (getattr(run, "before_thumb_path", None) or "").strip() else None,
        "afterThumbUrl": f"/api/overlay/{run.after_thumb_path}" if (getattr(run, "after_thumb_path", None) or "").strip() else None,
        "createdAt": _isoformat_ist(run.created_at),
    }


@app.post("/api/history/{run_id}/notify")
def notify_run(
    run_id: int,
    data: EmailRequest,
    db: Session = Depends(get_db),
):
    user = get_or_create_guest_user(db)
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id, DetectionRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    regions = _load_regions_json(run.regions_json)
    sent, error = send_notification(
        recipient=data.email.strip(),
        title=run.title,
        method=run.method,
        zone=run.zone or "",
        village=run.village or "",
        change_pct=float(run.change_percentage),
        changed_px=int(run.changed_pixels),
        total_px=int(run.total_pixels),
        regions=regions,
    )
    if not sent:
        raise HTTPException(status_code=400, detail=error or "Failed to send report email")
    return {"ok": True, "message": f"Report email sent to {data.email.strip()}."}


# --- Delete history run ---
@app.delete("/api/history/{run_id}")
def delete_run(
    run_id: int,
    db: Session = Depends(get_db),
):
    user = get_or_create_guest_user(db)
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id, DetectionRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    # Delete overlay and thumbnail files if they exist
    for path_attr in ("overlay_path", "before_full_path", "before_thumb_path", "after_thumb_path"):
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
    if IS_DDA_MODE:
        index_file = TEMPLATES_DIR / "index_dda.html"
    else:
        index_file = TEMPLATES_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>Satellite Change Detection</h1><p>Create <code>templates/index.html</code> and <code>static/</code>.</p>")
    return FileResponse(index_file)
