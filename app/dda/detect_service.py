"""Run change detection and persist results (shared by upload + library paths)."""
from __future__ import annotations

import base64
import json
import logging
import uuid
from datetime import timezone
from pathlib import Path
from typing import Optional

from PIL import Image
from sqlalchemy.orm import Session

from ..auth import get_or_create_guest_user
from ..database import DATA_DIR
from ..models import DetectionRun
from .geo_regions import enrich_regions_geo, resolve_geo_context

logger = logging.getLogger(__name__)

OVERLAYS_DIR = DATA_DIR / "overlays"
THUMB_MAX_SIZE = 200


def _serialize_regions(change_regions) -> list:
    return [
        {
            "id": int(r["id"]),
            "area": int(r["area"]),
            "center": {"x": round(float(r["center"][0])), "y": round(float(r["center"][1]))},
            "bbox": {
                "x": int(r["bbox"][0]),
                "y": int(r["bbox"][1]),
                "w": int(r["bbox"][2]),
                "h": int(r["bbox"][3]),
            },
            "objectType": str(r["object_type"]),
            "confidence": float(r["confidence"]),
            "severity": r.get("severity", "minor"),
            "subType": r.get("sub_type"),
            "subTypeConfidence": float(r["sub_type_confidence"])
            if r.get("sub_type_confidence") is not None
            else None,
            "estimatedStories": r.get("estimated_stories"),
            "estimatedHeightM": float(r["estimated_height_m"])
            if r.get("estimated_height_m") is not None
            else None,
            "constructionStage": r.get("construction_stage"),
        }
        for r in change_regions
    ]


def _isoformat_ist(dt):
    from datetime import timedelta
    if dt is None:
        return None
    _IST = timezone(timedelta(hours=5, minutes=30))
    if dt.tzinfo is None:
        from datetime import timezone as tz
        dt = dt.replace(tzinfo=tz.utc)
    return dt.astimezone(_IST).isoformat()


def run_detection_and_save(
    db: Session,
    before_pil: Image.Image,
    after_pil: Image.Image,
    *,
    method: str = "AI-Based Deep Learning",
    title: str = "Untitled run",
    zone: str = "",
    village: str = "",
    enable_registration: bool = True,
    enable_normalization: bool = True,
    detection_sensitivity: float = 0.5,
    min_region_area: Optional[int] = None,
    notify_email: Optional[str] = None,
    max_size: Optional[int] = None,
    geo_bounds_path: Optional[Path] = None,
    base_path: str = "",
    user_id: Optional[int] = None,
    job_id: Optional[int] = None,
) -> dict:
    from ..detection_engine import run_detection
    from .job_progress import update_job_progress

    def _report(pct: int, stage: str) -> None:
        if job_id is not None:
            update_job_progress(job_id, pct, stage)

    if user_id:
        from ..auth import get_user_by_id
        user = get_user_by_id(db, user_id)
        if not user:
            user = get_or_create_guest_user(db)
    else:
        user = get_or_create_guest_user(db)
    detection_sensitivity = max(0.0, min(1.0, float(detection_sensitivity)))
    if min_region_area is not None:
        min_region_area = int(max(50, min(10000, min_region_area)))

    def _on_engine_progress(engine_pct: int, stage: str) -> None:
        # Map engine 0–100% into job 15–78%
        job_pct = 15 + int(engine_pct * 0.63)
        _report(job_pct, stage)

    _report(15, "Running detection")
    change_mask, result_image, stats, change_regions = run_detection(
        before_pil,
        after_pil,
        method=method,
        enable_registration=enable_registration,
        enable_normalization=enable_normalization,
        detection_sensitivity=detection_sensitivity,
        min_region_area=min_region_area,
        max_size=max_size,
        on_progress=_on_engine_progress,
    )

    _report(80, "Saving results")
    from ..detection_engine import preprocess_image, get_detection_max_size

    before_for_slider = Image.fromarray(
        preprocess_image(before_pil, max_size=max_size or get_detection_max_size())
    )

    base_name = f"{user.id}_{uuid.uuid4().hex}"
    overlay_filename = base_name + ".png"
    overlay_path = OVERLAYS_DIR / overlay_filename
    overlay_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(result_image).save(overlay_path)
    relative_overlay = f"overlays/{overlay_filename}"

    relative_before_full = ""
    relative_before_thumb = ""
    relative_after_thumb = ""
    relative_after_full = ""
    try:
        after_for_slider = Image.fromarray(
            preprocess_image(after_pil, max_size=max_size or get_detection_max_size())
        )
        before_full_file = OVERLAYS_DIR / f"{base_name}_before.png"
        before_for_slider.save(before_full_file)
        relative_before_full = f"overlays/{base_name}_before.png"
        after_full_file = OVERLAYS_DIR / f"{base_name}_after.png"
        after_for_slider.save(after_full_file)
        relative_after_full = f"overlays/{base_name}_after.png"
        before_thumb_pil = before_pil.copy()
        before_thumb_pil.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.Resampling.LANCZOS)
        before_thumb_pil.save(OVERLAYS_DIR / f"{base_name}_before_thumb.png")
        after_thumb_pil = after_pil.copy()
        after_thumb_pil.thumbnail((THUMB_MAX_SIZE, THUMB_MAX_SIZE), Image.Resampling.LANCZOS)
        after_thumb_pil.save(OVERLAYS_DIR / f"{base_name}_after_thumb.png")
        relative_before_thumb = f"overlays/{base_name}_before_thumb.png"
        relative_after_thumb = f"overlays/{base_name}_after_thumb.png"
    except Exception as exc:
        logger.warning("Failed to save thumbnails: %s", exc)

    regions_serializable = _serialize_regions(change_regions)
    det_w = int(stats.get("image_width") or 0)
    det_h = int(stats.get("image_height") or 0)
    if det_w <= 0 or det_h <= 0:
        det_w, det_h = before_for_slider.size

    geo_ctx = None
    bounds = None
    if geo_bounds_path:
        rel_path = (base_path or "").replace("\\", "/").strip().lstrip("/")
        if not rel_path:
            try:
                from .config import get_storage_root
                rel_path = geo_bounds_path.resolve().relative_to(get_storage_root().resolve()).as_posix()
            except Exception:
                rel_path = geo_bounds_path.name
        geo_ctx = resolve_geo_context(db, rel_path, geo_bounds_path)
        bounds = geo_ctx.bounds

    regions_serializable = enrich_regions_geo(
        regions_serializable,
        img_width=det_w,
        img_height=det_h,
        bounds=bounds,
        geo=geo_ctx,
    )
    regions_with_coords = sum(1 for r in regions_serializable if r.get("latLng"))
    geo_debug = {
        "source": geo_ctx.source if geo_ctx else "none",
        "crs": str(geo_ctx.georef.crs) if (geo_ctx and geo_ctx.georef and geo_ctx.georef.crs) else "",
        "bounds": list(bounds) if bounds else None,
        "georefWidth": geo_ctx.georef_width if geo_ctx else 0,
        "georefHeight": geo_ctx.georef_height if geo_ctx else 0,
        "detectionWidth": det_w,
        "detectionHeight": det_h,
        "regionsWithCoords": regions_with_coords,
        "regionsTotal": len(regions_serializable),
    }
    logger.info("Geo debug for run: %s", geo_debug)
    total_px = int(stats["total_pixels"])
    changed_px = int(stats["changed_pixels"])
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
        after_full_path=relative_after_full,
        regions_json=json.dumps(regions_serializable),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    _report(90, "Preparing report")
    overlay_b64 = base64.b64encode(overlay_path.read_bytes()).decode("utf-8")
    notification_sent = False
    notification_error = None
    if notify_email and notify_email.strip():
        _report(95, "Sending notification")
        from .config import IS_DDA_MODE, get_public_base_url
        report_url = f"{get_public_base_url()}/dda/reports/{run.id}" if IS_DDA_MODE else ""
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
            report_url=report_url,
        )

    _report(100, "Complete")

    return {
        "id": run.id,
        "title": run.title,
        "method": run.method,
        "zone": run.zone or "",
        "village": run.village or "",
        "statistics": {
            "totalPixels": total_px,
            "changedPixels": changed_px,
            "unchangedPixels": int(stats["unchanged_pixels"]),
            "changePercentage": change_pct,
            "thresholdDebug": stats.get("threshold_debug", {}),
            "params": stats.get("params", {}),
            "alignmentWarning": stats.get("alignment_warning"),
            "registrationOk": stats.get("params", {}).get("registration_ok"),
            "geo": geo_debug,
        },
        "regions": regions_serializable,
        "overlayBase64Png": overlay_b64,
        "overlayUrl": f"/api/overlay/{relative_overlay}",
        "beforeFullUrl": f"/api/overlay/{relative_before_full}" if relative_before_full else None,
        "beforeThumbUrl": f"/api/overlay/{relative_before_thumb}" if relative_before_thumb else None,
        "afterThumbUrl": f"/api/overlay/{relative_after_thumb}" if relative_after_thumb else None,
        "afterFullUrl": f"/api/overlay/{relative_after_full}" if relative_after_full else None,
        "notificationSent": notification_sent,
        "notificationError": notification_error,
        "createdAt": _isoformat_ist(run.created_at),
        "detectionMaxSide": max_size,
    }
