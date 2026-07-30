"""False-positive training archive export (FR-08 Phase 2 / Phase 7)."""
from __future__ import annotations

import csv
import io
import json
import logging
from typing import Optional

import numpy as np
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
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


@router.post("/training/pack")
def export_training_pack(
    base_path: str = Form(...),
    comparison_path: str = Form(...),
    roi: Optional[str] = Form(None),
    run_id: Optional[int] = Form(None),
    det_w: Optional[int] = Form(None),
    det_h: Optional[int] = Form(None),
    max_side: int = Form(2048),
    zone: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Export a paint-ready GT-labeling pack from a pair (optionally an ROI).

    ``roi`` is an optional JSON ``{"x","y","w","h"}`` fractional window; when a
    ``run_id`` (+ its ``det_w``/``det_h``) is given, that run's regions seed the
    draft mask so labelers start from the detector's output.
    """
    _require_dda()
    from .geotiff_io import load_rgb_roi, parse_roi
    from .local_routes import safe_resolve
    from .training_pack import make_pair_id, rasterize_regions, write_labeling_pack

    base_norm = base_path.replace("\\", "/").strip()
    comp_norm = comparison_path.replace("\\", "/").strip()
    if not base_norm or not comp_norm:
        raise HTTPException(status_code=400, detail="base_path and comparison_path are required")
    base_file = safe_resolve(base_norm)
    comp_file = safe_resolve(comp_norm)

    roi_dict = None
    if roi and roi.strip():
        try:
            roi_dict = parse_roi(json.loads(roi))
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid roi: {exc}") from exc

    try:
        before = np.array(load_rgb_roi(base_file, roi_dict, max_side=max_side).convert("RGB"))
        after = np.array(load_rgb_roi(comp_file, roi_dict, max_side=max_side).convert("RGB"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not load images: {exc}") from exc
    if before.shape[:2] != after.shape[:2]:
        import cv2
        after = cv2.resize(after, (before.shape[1], before.shape[0]))

    # Seed the draft mask from an existing run's regions (scaled to the pack).
    seed = None
    if run_id is not None and det_w and det_h:
        run = db.query(DetectionRun).filter(DetectionRun.id == run_id).first()
        if run is not None:
            seed = rasterize_regions(
                load_regions(run), int(det_w), int(det_h),
                before.shape[1], before.shape[0])

    pair_id = make_pair_id(base_file.name, comp_file.name, roi_dict)
    result = write_labeling_pack(
        before, after, seed, pair_id=pair_id,
        before_path=base_file, after_path=comp_file, roi=roi_dict, zone=zone)
    logger.info("Exported training pack %s (seed_px=%s)", pair_id, result.get("seedChangedPx"))
    return {
        "ok": True,
        "ingestCmd": f"python scripts/ingest_dda_gt_label.py --pair-id {pair_id}",
        **result,
    }


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
