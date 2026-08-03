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
from ..notifier import send_notification
from .geo_regions import enrich_regions_geo, resolve_geo_context

logger = logging.getLogger(__name__)

OVERLAYS_DIR = DATA_DIR / "overlays"
THUMB_MAX_SIZE = 200


MAX_POLYGON_VERTICES = 100


def _serialize_polygon(raw) -> Optional[list]:
    """Normalize an engine polygon to ``[[x, y], ...]`` ints, or None.

    The engine emits an outer ring in detection-image pixels. Defensively caps
    the vertex count (the extractor also limits, but region JSON is stored per
    run and a runaway contour would bloat every report that loads it) by
    keeping evenly-spaced vertices so the footprint's shape survives.
    """
    if not raw:
        return None
    try:
        pts = [(int(round(float(p[0]))), int(round(float(p[1])))) for p in raw]
    except (TypeError, ValueError, IndexError):
        logger.warning("Ignoring malformed polygon on region")
        return None
    if len(pts) < 3:
        return None
    if len(pts) > MAX_POLYGON_VERTICES:
        step = len(pts) / float(MAX_POLYGON_VERTICES)
        pts = [pts[min(len(pts) - 1, int(i * step))] for i in range(MAX_POLYGON_VERTICES)]
    return [[x, y] for x, y in pts]


def _serialize_regions(change_regions) -> list:
    out = []
    for r in change_regions:
        entry = {
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
        # Polygon is optional: regions without one serialize exactly as before,
        # and every consumer falls back to bbox.
        polygon = _serialize_polygon(r.get("polygon"))
        if polygon:
            entry["polygon"] = polygon
        out.append(entry)
    return out


def _filter_weak_other_regions(regions: list) -> list:
    """Optionally mark tiny low-conf Other blobs — never drop report rows.

    History: a hard conf floor (DDA_OTHER_MIN_CONF=0.5) deleted 13/15 Grid_54
    regions from ``regions_json`` while the overlay still drew them, so the
    report disagreed with detection. Regions must match the engine list; ops
    can hide noise in review instead of deleting at save time.

    Set ``DDA_OTHER_MIN_CONF>0`` and ``DDA_SUPPRESS_WEAK_OTHER=1`` to flag
    (not remove) tiny Other blobs with ``suppressed=true``.
    """
    import os
    raw = os.environ.get("DDA_OTHER_MIN_CONF", "0").strip()
    try:
        floor = float(raw)
    except ValueError:
        floor = 0.0
    try:
        min_area = int(float(os.environ.get("DDA_OTHER_MIN_AREA", "1200")))
    except ValueError:
        min_area = 1200
    suppress = os.environ.get("DDA_SUPPRESS_WEAK_OTHER", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if floor <= 0:
        for r in regions:
            r.setdefault("suppressed", False)
        return regions

    kept = []
    flagged = 0
    for r in regions:
        dda = (r.get("ddaChangeType") or "").strip()
        conf = float(r.get("confidence") or 0.0)
        area = int(r.get("area") or 0)
        weak = dda == "Other" and conf < floor and area < min_area
        r = dict(r)
        r["suppressed"] = bool(weak and suppress)
        if weak and suppress:
            flagged += 1
        # Never drop — always keep in report JSON
        kept.append(r)
    if flagged:
        logger.info("Flagged %d weak Other regions as suppressed (still in report)", flagged)
    return kept


def _isoformat_ist(dt):
    from datetime import timedelta
    if dt is None:
        return None
    _IST = timezone(timedelta(hours=5, minutes=30))
    if dt.tzinfo is None:
        from datetime import timezone as tz
        dt = dt.replace(tzinfo=tz.utc)
    return dt.astimezone(_IST).isoformat()


def _alignment_warning(before_pil, after_pil, before_tif, after_tif):
    """Operator-facing warning when a pair is identical or poorly aligned.

    Runs the pre-detection guard (``app.dda.pair_align``) to catch the two
    silent-failure modes that otherwise yield an empty or garbage change mask
    with no explanation: (1) the same image uploaded twice, and (2) raw drone
    frames from different viewpoints that never register onto a common grid.
    Returns ``None`` when the pair looks fine or the check cannot run, so this
    can never block or crash a detection job.
    """
    try:
        import numpy as np

        from .pair_align import are_identical, assess_alignment, geo_align_pair

        b = np.asarray(before_pil.convert("RGB"))
        a = np.asarray(after_pil.convert("RGB"))
        if b.shape == a.shape and are_identical(b, a):
            return ("Before and after are the same image (identical pixels). There is "
                    "no change to detect - please check that two different dates were "
                    "selected.")
        # The NCC misalignment check only makes sense on georeferenced pairs, where
        # both can be placed on a common grid. For plain uploads the engine's own
        # SIFT/ECC registration handles alignment, so comparing the raw (pre-
        # registration) frames here would raise false alarms.
        if before_tif and after_tif:
            aligned = geo_align_pair(Path(before_tif), Path(after_tif))
            if aligned is not None:
                bb, aa, _overlap = aligned
                status, ncc = assess_alignment(bb, aa)
                if status == "low_quality":
                    return (f"The before/after images do not align well (NCC={ncc:.2f}). "
                            "They look like raw drone frames from different viewpoints "
                            "rather than orthomosaics on a common grid, so detected "
                            "changes may be unreliable. For accurate results, export both "
                            "dates as north-up orthomosaics on the same grid.")
        return None
    except Exception as exc:
        logger.warning("Alignment guard skipped: %s", exc)
        return None


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
    comparison_file: Optional[Path] = None,
    base_path: str = "",
    user_id: Optional[int] = None,
    job_id: Optional[int] = None,
    gsd_debug: Optional[dict] = None,
    shape_mode: str = "polygon",
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

    def _geotiff_path(p: Optional[Path]) -> Optional[str]:
        if p is not None and str(p).lower().endswith((".tif", ".tiff")):
            return str(p)
        return None

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
        before_path=_geotiff_path(geo_bounds_path),
        after_path=_geotiff_path(comparison_file),
        shape_mode=shape_mode,
    )
    # Record the choice so the viewer can match the baked overlay's style.
    if isinstance(stats.get("params"), dict):
        stats["params"]["shape_mode"] = shape_mode

    # Pre-detection guard: surface identical / poorly-aligned pairs to the operator
    # instead of returning a silent empty or garbage mask (the UI already renders
    # stats["alignment_warning"] as a banner). Never overrides an existing warning.
    if not stats.get("alignment_warning"):
        align_warn = _alignment_warning(
            before_pil, after_pil,
            _geotiff_path(geo_bounds_path), _geotiff_path(comparison_file),
        )
        if align_warn:
            stats["alignment_warning"] = align_warn
            logger.info("Alignment guard flagged pair: %s", align_warn)

    if gsd_debug and isinstance(stats.get("threshold_debug"), dict):
        stats["threshold_debug"]["gsdHarmonization"] = gsd_debug

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

    relative_prob = ""
    try:
        from ..detection_config import get_save_prob_map
        score_map = stats.pop("_score_map", None)
        if get_save_prob_map() and score_map is not None:
            import numpy as _np
            prob_u8 = _np.clip(score_map * 255.0, 0, 255).astype("uint8")
            prob_img = Image.fromarray(prob_u8)
            prob_img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            prob_file = OVERLAYS_DIR / f"{base_name}_prob.png"
            prob_img.save(prob_file)
            relative_prob = f"overlays/{base_name}_prob.png"
    except Exception as exc:
        logger.warning("Probability map export failed: %s", exc)

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
    # Ops filter: PDF report (Grid_54 vs H43X2E1) showed many low-conf "Other"
    # blobs at thr~0.2 — quarantine weak unclassified FPs without raising DL thr.
    before_n = len(regions_serializable)
    regions_serializable = _filter_weak_other_regions(regions_serializable)
    dropped_other = before_n - len(regions_serializable)
    if dropped_other:
        logger.info("Filtered %d low-confidence Other regions", dropped_other)
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
        regions_count=len(regions_serializable),
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
            "probabilityMapUrl": f"/api/overlay/{relative_prob}" if relative_prob else None,
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
