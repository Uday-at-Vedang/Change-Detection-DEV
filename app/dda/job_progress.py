"""Persist detection job progress for UI polling."""
from __future__ import annotations

import json
import logging

from ..database import SessionLocal
from .models import DetectionJob

logger = logging.getLogger(__name__)


def update_job_progress(job_id: int, pct: int, stage: str) -> None:
    """Update progress_pct and progress_stage in job params_json."""
    db = SessionLocal()
    try:
        job = db.query(DetectionJob).filter(DetectionJob.id == job_id).first()
        if not job or job.status not in ("queued", "running"):
            return
        params = {}
        try:
            params = json.loads(job.params_json or "{}")
        except json.JSONDecodeError:
            pass
        params["progress_pct"] = max(0, min(100, int(pct)))
        params["progress_stage"] = stage or ""
        job.params_json = json.dumps(params)
        db.commit()
    except Exception as exc:
        logger.warning("Could not update job %d progress: %s", job_id, exc)
        db.rollback()
    finally:
        db.close()


def get_job_progress(params: dict, status: str) -> tuple[int, str]:
    pct = params.get("progress_pct")
    stage = params.get("progress_stage") or ""
    if status == "completed":
        return 100, stage or "Complete"
    if status == "failed":
        return int(pct) if pct is not None else 0, stage or "Failed"
    if status == "queued":
        return int(pct) if pct is not None else 0, stage or "Queued"
    if pct is None:
        return 10, stage or "Running"
    return int(pct), stage or "Running"
