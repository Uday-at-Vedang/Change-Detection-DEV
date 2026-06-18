"""DDA report browser view, PDF export, and email notify (FR-05)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..auth import get_or_create_guest_user
from ..database import get_db
from ..models import DetectionRun
from ..notifier import send_notification
from .config import get_public_base_url
from .report_pdf import build_report_dict, generate_report_pdf
from .review_service import load_regions, merge_reviews

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


@router.get("/reports/{run_id}")
def get_report(run_id: int, db: Session = Depends(get_db)):
    """JSON payload for the standalone report page and API clients."""
    _require_dda()
    user = get_or_create_guest_user(db)
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    data = build_report_dict(run, include_overlay_b64=True, regions=regions)
    data["reportUrl"] = f"{get_public_base_url()}/dda/reports/{run.id}"
    return data


@router.get("/reports/{run_id}/pdf")
def download_report_pdf(run_id: int, db: Session = Depends(get_db)):
    """Download detection report as PDF."""
    _require_dda()
    user = get_or_create_guest_user(db)
    run = _get_user_run(db, run_id, user.id)
    try:
        pdf_bytes, filename = generate_report_pdf(run)
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="PDF export is not available (reportlab missing)") from exc
    except Exception as exc:
        logger.exception("PDF generation failed for run %s", run_id)
        raise HTTPException(status_code=500, detail=f"PDF generation failed: {exc}") from exc
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class ReportNotifyBody(BaseModel):
    email: EmailStr


@router.post("/reports/{run_id}/notify")
def notify_report(run_id: int, body: ReportNotifyBody, db: Session = Depends(get_db)):
    """Email report summary with link to the browser report page."""
    _require_dda()
    user = get_or_create_guest_user(db)
    run = _get_user_run(db, run_id, user.id)
    import json
    regions = json.loads(run.regions_json or "[]")
    report_url = f"{get_public_base_url()}/dda/reports/{run.id}"
    sent, error = send_notification(
        recipient=body.email.strip(),
        title=run.title,
        method=run.method,
        zone=run.zone or "",
        village=run.village or "",
        change_pct=float(run.change_percentage),
        changed_px=int(run.changed_pixels),
        total_px=int(run.total_pixels),
        regions=regions,
        report_url=report_url,
    )
    if not sent:
        raise HTTPException(status_code=400, detail=error or "Failed to send report email")
    return {"ok": True, "message": f"Report link sent to {body.email.strip()}.", "reportUrl": report_url}
