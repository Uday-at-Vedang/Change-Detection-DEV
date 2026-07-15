"""Background detection job runner (FR-04 async pipeline)."""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image
from sqlalchemy.orm import Session

from ..auth import get_or_create_guest_user
from ..database import SessionLocal
from ..models import DetectionRun
from .config import get_detection_max_side
from .detect_service import run_detection_and_save
from .geotiff_io import load_rgb_pil
from .job_progress import update_job_progress
from .local_routes import safe_resolve
from .models import DetectionJob

logger = logging.getLogger(__name__)

_runner_lock = threading.Lock()
_active_job_id: Optional[int] = None


def _utcnow():
    return datetime.now(timezone.utc)


def _load_pair(
    base_path: str, comparison_path: str,
) -> tuple[Image.Image, Image.Image, Path, Optional[dict]]:
    from ..detection_config import get_load_max_side
    base_file = safe_resolve(base_path)
    comp_file = safe_resolve(comparison_path)
    max_side = get_load_max_side()
    before_pil = load_rgb_pil(base_file, max_side=max_side)
    after_pil = load_rgb_pil(comp_file, max_side=max_side)

    gsd_debug = None
    try:
        from .geotiff_io import harmonize_pair_gsd
        before_pil, after_pil, gsd_debug = harmonize_pair_gsd(
            before_pil, after_pil, base_file, comp_file)
    except Exception as exc:
        logger.warning("GSD harmonization skipped: %s", exc)

    if before_pil.size != after_pil.size:
        after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)
    return before_pil, after_pil, base_file, gsd_debug


def _parse_params(job: DetectionJob) -> Dict[str, Any]:
    try:
        return json.loads(job.params_json or "{}")
    except json.JSONDecodeError:
        return {}


def _run_job_sync(job_id: int) -> None:
    global _active_job_id
    db = SessionLocal()
    try:
        job = db.query(DetectionJob).filter(DetectionJob.id == job_id).first()
        if not job or job.status not in ("queued", "running"):
            return

        job.status = "running"
        job.started_at = _utcnow()
        job.error_message = ""
        db.commit()
        update_job_progress(job_id, 5, "Starting job")

        params = _parse_params(job)
        base_path = params.get("base_path", "")
        comparison_path = params.get("comparison_path", "")
        if not base_path or not comparison_path:
            raise ValueError("Job missing base_path or comparison_path in params_json")

        update_job_progress(job_id, 8, "Loading images")
        before_pil, after_pil, base_file, gsd_debug = _load_pair(base_path, comparison_path)
        comp_file = safe_resolve(comparison_path)
        update_job_progress(job_id, 12, "Images loaded")
        title = params.get("title") or f"{Path(base_path).name} vs {Path(comparison_path).name}"
        result = run_detection_and_save(
            db,
            before_pil,
            after_pil,
            method=job.method or params.get("method", "AI-Based Deep Learning"),
            title=title,
            zone=params.get("zone", ""),
            village=params.get("village", ""),
            enable_registration=bool(params.get("enable_registration", True)),
            enable_normalization=bool(params.get("enable_normalization", True)),
            detection_sensitivity=float(params.get("detection_sensitivity", 0.45)),
            min_region_area=params.get("min_region_area"),
            notify_email=job.notify_email or params.get("notify_email"),
            max_size=get_detection_max_side(),
            geo_bounds_path=base_file,
            comparison_file=comp_file,
            base_path=base_path,
            user_id=job.created_by,
            job_id=job_id,
            gsd_debug=gsd_debug,
        )

        update_job_progress(job_id, 100, "Complete")

        job.status = "completed"
        job.run_id = result["id"]
        job.completed_at = _utcnow()
        db.commit()
        logger.info("Detection job %d completed → run %s", job_id, result["id"])
    except Exception as exc:
        logger.exception("Detection job %d failed", job_id)
        try:
            job = db.query(DetectionJob).filter(DetectionJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)[:2000]
                job.completed_at = _utcnow()
                db.commit()
        except Exception:
            db.rollback()
    finally:
        with _runner_lock:
            if _active_job_id == job_id:
                _active_job_id = None
        db.close()


def _job_worker(job_id: int) -> None:
    global _active_job_id
    with _runner_lock:
        _active_job_id = job_id
    try:
        _run_job_sync(job_id)
    finally:
        with _runner_lock:
            if _active_job_id == job_id:
                _active_job_id = None


def enqueue_detection_job(job_id: int) -> bool:
    """Start job in a background thread. Returns False if another job is running."""
    global _active_job_id
    with _runner_lock:
        if _active_job_id is not None:
            return False
        _active_job_id = job_id
    thread = threading.Thread(target=_job_worker, args=(job_id,), daemon=True, name=f"dda-job-{job_id}")
    thread.start()
    return True


def is_job_runner_busy() -> bool:
    with _runner_lock:
        return _active_job_id is not None


def reconcile_stale_jobs(db: Session) -> int:
    """Mark orphaned running jobs failed after server restart; re-queue oldest queued job."""
    if is_job_runner_busy():
        return 0
    fixed = 0
    running = db.query(DetectionJob).filter(DetectionJob.status == "running").all()
    for job in running:
        job.status = "failed"
        job.error_message = "Job interrupted (server restarted). Please run detection again."
        job.completed_at = _utcnow()
        fixed += 1
    if fixed:
        db.commit()
        logger.info("Reconciled %d stale running job(s)", fixed)

    if not is_job_runner_busy():
        next_queued = (
            db.query(DetectionJob)
            .filter(DetectionJob.status == "queued")
            .order_by(DetectionJob.created_at.asc())
            .first()
        )
        if next_queued:
            enqueue_detection_job(next_queued.id)
    return fixed


def create_local_folder_job(
    db: Session,
    *,
    base_path: str,
    comparison_path: str,
    method: str = "AI-Based Deep Learning",
    title: str = "",
    zone: str = "",
    village: str = "",
    enable_registration: bool = True,
    enable_normalization: bool = True,
    detection_sensitivity: float = 0.45,
    min_region_area: Optional[int] = 150,
    notify_email: str = "",
    created_by: Optional[int] = None,
) -> DetectionJob:
    user = get_or_create_guest_user(db)
    params = {
        "source": "local_folder",
        "base_path": base_path.replace("\\", "/"),
        "comparison_path": comparison_path.replace("\\", "/"),
        "method": method,
        "title": title,
        "zone": zone,
        "village": village,
        "enable_registration": enable_registration,
        "enable_normalization": enable_normalization,
        "detection_sensitivity": detection_sensitivity,
        "min_region_area": min_region_area,
    }
    job = DetectionJob(
        status="queued",
        base_image_id=None,
        comparison_image_id=None,
        method=method,
        params_json=json.dumps(params),
        notify_email=notify_email or "",
        created_by=created_by or user.id,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def job_to_dict(job: DetectionJob, run: Optional[DetectionRun] = None) -> dict:
    from .job_progress import get_job_progress

    params = _parse_params(job)
    progress_pct, progress_stage = get_job_progress(params, job.status)
    out = {
        "id": job.id,
        "status": job.status,
        "method": job.method,
        "basePath": params.get("base_path", ""),
        "comparisonPath": params.get("comparison_path", ""),
        "title": params.get("title", ""),
        "runId": job.run_id,
        "errorMessage": job.error_message or "",
        "notifyEmail": job.notify_email or "",
        "progressPct": progress_pct,
        "progressStage": progress_stage,
        "createdAt": job.created_at.isoformat() if job.created_at else None,
        "startedAt": job.started_at.isoformat() if job.started_at else None,
        "completedAt": job.completed_at.isoformat() if job.completed_at else None,
    }
    if run:
        out["report"] = {
            "id": run.id,
            "title": run.title,
            "changePercentage": run.change_percentage,
            "regionsCount": run.regions_count,
            "overlayUrl": f"/api/overlay/{run.overlay_path}" if run.overlay_path else None,
        }
    return out
