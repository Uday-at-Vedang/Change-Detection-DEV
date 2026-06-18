"""Region review and departmental export API (FR-08)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DetectionRun, User
from .dda_auth import current_dda_user
from .dept_export import ExportResult, get_exporter, regions_to_csv_rows
from .review_service import (
    filter_regions_by_review,
    load_regions,
    mark_confirmed_submitted,
    merge_reviews,
    review_summary,
    set_region_review,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


def _get_user_run(db: Session, run_id: int, user_id: int) -> DetectionRun:
    run = db.query(DetectionRun).filter(
        DetectionRun.id == run_id,
        DetectionRun.user_id == user_id,
    ).first()
    if not run:
        raise HTTPException(status_code=404, detail="Report not found")
    return run


class RegionReviewBody(BaseModel):
    reviewStatus: str
    notes: Optional[str] = ""


@router.patch("/reports/{run_id}/regions/{region_id}")
def patch_region_review(
    run_id: int,
    region_id: int,
    body: RegionReviewBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Mark a region as confirmed or false positive."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    if body.reviewStatus not in ("confirmed", "false_positive", "pending"):
        raise HTTPException(status_code=400, detail="reviewStatus must be confirmed, false_positive, or pending")
    try:
        updated = set_region_review(
            db, run, region_id, body.reviewStatus, user.id, body.notes or ""
        )
    except LookupError:
        raise HTTPException(status_code=404, detail="Region not found")
    except PermissionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "region": updated, "summary": review_summary(merge_reviews(db, run.id, load_regions(run)))}


@router.get("/reports/{run_id}/review-summary")
def get_review_summary(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    return {"runId": run.id, "summary": review_summary(regions)}


@router.get("/reports/{run_id}/export.csv")
def export_csv(
    run_id: int,
    confirmed: int = Query(0, ge=0, le=1),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Export regions as CSV. Use ?confirmed=1 for confirmed-only."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    if confirmed:
        regions = filter_regions_by_review(regions, "confirmed")
    if not regions:
        raise HTTPException(status_code=404, detail="No regions to export")
    csv_text = regions_to_csv_rows(run, regions)
    suffix = "confirmed" if confirmed else "all"
    filename = f"dda_run_{run_id}_{suffix}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/reports/{run_id}/submit")
def submit_confirmed(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """Submit confirmed regions to departmental API (or file fallback)."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    confirmed = filter_regions_by_review(regions, "confirmed")
    if not confirmed:
        raise HTTPException(status_code=400, detail="No confirmed regions to submit. Review regions first.")

    exporter = get_exporter()
    result: ExportResult = exporter.submit(run, confirmed)
    if not result.ok:
        raise HTTPException(status_code=502, detail=result.message)

    submitted_ids = [int(r["id"]) for r in confirmed if r.get("id") is not None]
    mark_confirmed_submitted(db, run.id, submitted_ids)

    return {
        "ok": True,
        "mode": result.mode,
        "message": result.message,
        "submittedCount": result.submitted_count,
        "downloadUrl": result.download_path,
        "detail": result.detail,
    }
