"""Async detection job API (FR-04)."""
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DetectionRun, User
from .dda_auth import current_dda_user
from .job_runner import (
    create_local_folder_job,
    enqueue_detection_job,
    is_job_runner_busy,
    job_to_dict,
)
from .local_library import safe_resolve
from .models import DetectionJob

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


@router.post("/jobs")
async def create_job(
    base_path: str = Form(...),
    comparison_path: str = Form(...),
    method: str = Form("AI-Based Deep Learning"),
    title: str = Form(""),
    zone: str = Form(""),
    village: str = Form(""),
    enable_registration: bool = Form(True),
    enable_normalization: bool = Form(True),
    detection_sensitivity: float = Form(0.45),
    min_region_area: Optional[int] = Form(150),
    notify_email: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Queue async detection from local library paths. Returns immediately with jobId."""
    _require_dda()
    base_norm = base_path.replace("\\", "/").strip()
    comp_norm = comparison_path.replace("\\", "/").strip()
    if not base_norm or not comp_norm:
        raise HTTPException(status_code=400, detail="base_path and comparison_path are required")
    if base_norm == comp_norm:
        raise HTTPException(status_code=400, detail="Base and comparison images must be different")

    try:
        safe_resolve(base_norm)
        safe_resolve(comp_norm)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid library path: {exc}") from exc

    if is_job_runner_busy():
        raise HTTPException(
            status_code=409,
            detail="Another detection job is already running. Wait for it to finish, then try again.",
        )

    if not title.strip():
        from pathlib import Path
        title = f"{Path(base_norm).name} vs {Path(comp_norm).name}"

    job = create_local_folder_job(
        db,
        base_path=base_norm,
        comparison_path=comp_norm,
        method=method,
        title=title,
        zone=zone,
        village=village,
        enable_registration=enable_registration,
        enable_normalization=enable_normalization,
        detection_sensitivity=detection_sensitivity,
        min_region_area=min_region_area,
        notify_email=notify_email or "",
        created_by=user.id,
    )

    if not enqueue_detection_job(job.id):
        job.status = "failed"
        job.error_message = "Could not start background worker"
        db.commit()
        raise HTTPException(status_code=503, detail="Job queue is busy")

    return {"jobId": job.id, "status": "queued", "message": "Detection job queued. Poll GET /api/dda/jobs/{id} for status."}


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    job = db.query(DetectionJob).filter(DetectionJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.created_by and job.created_by != user.id:
        raise HTTPException(status_code=403, detail="Not allowed to view this job")

    run = None
    if job.run_id:
        run = db.query(DetectionRun).filter(DetectionRun.id == job.run_id).first()

    data = job_to_dict(job, run=run)
    if job.status == "completed" and run:
        try:
            data["result"] = _run_detail(db, run, user.id)
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Could not load full run for job %s: %s", job_id, exc)
            data["resultError"] = str(exc)[:500]
    return data


def _run_detail(db: Session, run: DetectionRun, user_id: int) -> dict:
    import base64

    from ..database import DATA_DIR

    if run.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not allowed")

    regions = json.loads(run.regions_json or "[]")
    from .review_service import merge_reviews
    regions = merge_reviews(db, run.id, regions)
    overlay_b64 = ""
    if run.overlay_path:
        overlay_file = DATA_DIR / run.overlay_path
        if overlay_file.exists():
            overlay_b64 = base64.b64encode(overlay_file.read_bytes()).decode("utf-8")

    from .config import get_detection_max_side
    from .detect_service import _isoformat_ist

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
        "overlayBase64Png": overlay_b64,
        "overlayUrl": f"/api/overlay/{run.overlay_path}" if run.overlay_path else None,
        "beforeFullUrl": f"/api/overlay/{run.before_full_path}" if run.before_full_path else None,
        "beforeThumbUrl": f"/api/overlay/{run.before_thumb_path}" if run.before_thumb_path else None,
        "afterThumbUrl": f"/api/overlay/{run.after_thumb_path}" if run.after_thumb_path else None,
        "afterFullUrl": f"/api/overlay/{run.after_full_path}" if getattr(run, "after_full_path", None) else None,
        "createdAt": _isoformat_ist(run.created_at),
        "detectionMaxSide": get_detection_max_side(),
    }


@router.get("/jobs")
def list_jobs(
    status: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Recent detection jobs for in-app notifications / reports feed (FR-05 partial)."""
    _require_dda()
    from .job_runner import reconcile_stale_jobs
    reconcile_stale_jobs(db)
    q = db.query(DetectionJob).filter(DetectionJob.created_by == user.id)
    if status:
        q = q.filter(DetectionJob.status == status)
    jobs = q.order_by(DetectionJob.created_at.desc()).limit(limit).all()
    out = []
    for job in jobs:
        run = db.query(DetectionRun).filter(DetectionRun.id == job.run_id).first() if job.run_id else None
        out.append(job_to_dict(job, run=run))
    return {"jobs": out, "runnerBusy": is_job_runner_busy()}
