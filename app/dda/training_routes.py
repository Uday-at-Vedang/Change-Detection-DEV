"""False-positive training archive export (FR-08 Phase 2 / Phase 7)."""
from __future__ import annotations

import csv
import io
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DetectionRun, User
from .dda_auth import current_dda_user, require_admin_or_key
from .geo_regions import region_lat_lng
from .models import RegionReview
from .review_service import load_regions

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


@router.get("/training/export")
def export_false_positives(
    request: Request,
    fmt: str = Query("csv"),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Export all false-positive regions for model training (admin or export key)."""
    _require_dda()
    require_admin_or_key(request, user, db)

    if fmt not in ("csv", "json"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="fmt must be csv or json")

    reviews = (
        db.query(RegionReview)
        .filter(RegionReview.status == "false_positive")
        .order_by(RegionReview.reviewed_at.desc())
        .all()
    )
    rows = []
    for rev in reviews:
        run = db.query(DetectionRun).filter(DetectionRun.id == rev.run_id).first()
        if not run:
            continue
        regions = load_regions(run)
        region = next((r for r in regions if int(r.get("id", -1)) == rev.region_id), None)
        if not region:
            continue
        lat, lng = region_lat_lng(region)
        rows.append({
            "runId": run.id,
            "runTitle": run.title,
            "regionId": rev.region_id,
            "ddaChangeType": region.get("ddaChangeType") or region.get("objectType"),
            "internalType": region.get("internalObjectType") or region.get("objectType"),
            "confidence": region.get("confidence"),
            "areaPx": region.get("area"),
            "latitude": lat,
            "longitude": lng,
            "notes": rev.notes or "",
            "reviewedAt": rev.reviewed_at.isoformat() if rev.reviewed_at else None,
        })

    if fmt == "json":
        return {"count": len(rows), "falsePositives": rows}

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "run_id", "run_title", "region_id", "dda_change_type", "internal_type",
        "confidence", "area_px", "latitude", "longitude", "notes", "reviewed_at",
    ])
    for r in rows:
        writer.writerow([
            r["runId"], r["runTitle"], r["regionId"], r["ddaChangeType"], r["internalType"],
            r.get("confidence"), r.get("areaPx"), r.get("latitude"), r.get("longitude"),
            r.get("notes"), r.get("reviewedAt"),
        ])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="dda_false_positives.csv"'},
    )
