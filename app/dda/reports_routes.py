"""Vedangsoft report browser view, PDF export, and email notify (FR-05)."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DetectionRun, User
from ..notifier import send_notification
from .dda_auth import current_dda_user
from .config import get_public_base_url
from .rbac.permissions import require_module_permission
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
def get_report(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """JSON payload for the standalone report page and API clients."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    data = build_report_dict(run, include_overlay_b64=True, regions=regions)
    data["reportUrl"] = f"{get_public_base_url()}/dda/reports/{run.id}"
    return data


def _region_geometry(region: dict) -> Optional[dict]:
    """GeoJSON geometry for a region: true polygon footprint, else bbox corners.

    ``polygonGeo`` is the projected outer ring (see geo_regions.polygon_to_lat_lng).
    Older runs — and runs whose georeferencing could not resolve — have only a
    ``latLng`` centre, so they degrade to a Point rather than being dropped.
    """
    ring = region.get("polygonGeo")
    if ring and len(ring) >= 3:
        coords = [[float(p["lng"]), float(p["lat"])] for p in ring
                  if isinstance(p, dict) and "lat" in p and "lng" in p]
        if len(coords) >= 3:
            if coords[0] != coords[-1]:
                coords.append(coords[0])  # GeoJSON rings must close
            return {"type": "Polygon", "coordinates": [coords]}
    ll = region.get("latLng") or {}
    if isinstance(ll, dict) and ll.get("lat") is not None and ll.get("lng") is not None:
        return {"type": "Point", "coordinates": [float(ll["lng"]), float(ll["lat"])]}
    return None


@router.get("/reports/{run_id}/export.geojson")
def export_report_geojson(
    run_id: int,
    confirmed: int = 0,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Export detected regions as GeoJSON (polygon footprints, bbox/point fallback)."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    if confirmed:
        regions = [r for r in regions if (r.get("reviewStatus") or "") == "confirmed"]

    features = []
    for r in regions:
        geometry = _region_geometry(r)
        if geometry is None:
            continue  # ungeoreferenced run — nothing meaningful to place on a map
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "id": r.get("id"),
                "ddaChangeType": r.get("ddaChangeType"),
                "objectType": r.get("objectType"),
                "confidence": r.get("confidence"),
                "areaPx": r.get("area"),
                "polygonAreaPx": r.get("polygonAreaPx"),
                "areaSqM": r.get("areaSqM"),
                "severity": r.get("severity"),
                "reviewStatus": r.get("reviewStatus", "pending"),
            },
        })

    payload = {
        "type": "FeatureCollection",
        "name": f"dda_run_{run.id}",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:OGC:1.3:CRS84"}},
        "features": features,
    }
    import json as _json
    return Response(
        content=_json.dumps(payload, indent=2),
        media_type="application/geo+json",
        headers={"Content-Disposition":
                 f'attachment; filename="dda_run_{run.id}_regions.geojson"'},
    )


@router.get("/reports/{run_id}/pdf")
def download_report_pdf(run_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """Download detection report as PDF."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
    try:
        pdf_bytes, filename = generate_report_pdf(run, regions=regions)
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
def notify_report(run_id: int, body: ReportNotifyBody, db: Session = Depends(get_db), user: User = Depends(require_module_permission("reports", "edit"))):
    """Email report summary with link to the browser report page."""
    _require_dda()
    run = _get_user_run(db, run_id, user.id)
    regions = merge_reviews(db, run.id, load_regions(run))
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
