"""Vedangsoft admin diagnostics and maintenance (Phase 7)."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from ..database import DATA_DIR, get_db
from ..models import DetectionRun, User
from .dda_auth import get_user_role
from .job_runner import is_job_runner_busy, reconcile_stale_jobs
from .tree.image_service import list_all_images
from .models import DetectionJob, RegionReview
from .rbac.permissions import require_module_permission

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


@router.get("/admin/status")
def admin_status(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("admin", "view")),
):
    """System status for UAT / ops."""
    _require_dda()

    runs = db.query(DetectionRun).count()
    jobs_queued = db.query(DetectionJob).filter(DetectionJob.status == "queued").count()
    jobs_running = db.query(DetectionJob).filter(DetectionJob.status == "running").count()
    fp_count = db.query(RegionReview).filter(RegionReview.status == "false_positive").count()

    disk = shutil.disk_usage(DATA_DIR)
    return {
        "role": get_user_role(db, user),
        "libraryImages": len(list_all_images(db)),
        "detectionRuns": runs,
        "jobsQueued": jobs_queued,
        "jobsRunning": jobs_running,
        "runnerBusy": is_job_runner_busy(),
        "falsePositiveArchive": fp_count,
        "dataDir": str(DATA_DIR),
        "diskFreeGb": round(disk.free / (1024 ** 3), 2),
        "diskTotalGb": round(disk.total / (1024 ** 3), 2),
    }


@router.post("/admin/reconcile-jobs")
def admin_reconcile_jobs(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("admin", "edit")),
):
    _require_dda()
    fixed = reconcile_stale_jobs(db)
    return {"ok": True, "staleRunningFixed": fixed}


@router.delete("/admin/purge-runs")
def admin_purge_old_runs(
    request: Request,
    days: int = Query(90, ge=7, le=3650),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("admin", "delete")),
):
    """Delete detection runs older than N days (admin only)."""
    _require_dda()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old = db.query(DetectionRun).filter(DetectionRun.created_at < cutoff).all()
    count = 0
    overlays_root = DATA_DIR
    for run in old:
        db.query(RegionReview).filter(RegionReview.run_id == run.id).delete()
        for job in db.query(DetectionJob).filter(DetectionJob.run_id == run.id).all():
            job.run_id = None
        for path_attr in ("overlay_path", "before_full_path", "before_thumb_path", "after_thumb_path", "after_full_path"):
            path_val = getattr(run, path_attr, None)
            if path_val:
                f = overlays_root / path_val
                if f.exists():
                    f.unlink(missing_ok=True)
        db.delete(run)
        count += 1
    db.commit()
    return {"ok": True, "purgedRuns": count, "olderThanDays": days}
