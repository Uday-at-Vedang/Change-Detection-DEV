"""Preflight image-pair validation: CRS, GSD, RGB bands, overlap, georeferencing.

Runs once per detection request, right after library paths are resolved but
before either image is pixel-decoded — all checks here are cheap header-only
reads, so a genuinely unusable pair (unreadable file, unsupported band
layout, no ground overlap) is rejected in milliseconds instead of after
minutes of registration + model inference. Lesser issues (grayscale input,
manual-bounds-only georeferencing, GSD mismatch, weak overlap) don't block
detection — they're collected as warnings and surfaced on the result instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from sqlalchemy.orm import Session

from ..detection_config import (
    get_gsd_tolerance,
    get_max_aspect_ratio_diff,
    get_min_overlap_hard,
    get_min_overlap_warn,
)
from .geo_regions import BoundsWGS84, resolve_geo_context
from .geotiff_io import inspect_image, read_georef, read_gsd_meters

import logging

logger = logging.getLogger(__name__)

_MODE_BAND_COUNTS = {
    "1": 1, "L": 1, "I": 1, "F": 1,
    "LA": 2,
    "RGB": 3, "YCbCr": 3, "P": 3,
    "RGBA": 4, "CMYK": 4,
}

_NON_RGB_BAND_KEYWORDS = ("nir", "infrared", "swir", "thermal", "alpha", "panchromatic")


@dataclass
class CheckResult:
    name: str        # readability|bands|georef|crs|gsd|overlap
    status: str       # pass|warn|fail
    message: str = ""


@dataclass
class PreflightResult:
    hard_fail: bool = False
    fail_reason: str = ""
    warnings: List[str] = field(default_factory=list)
    checks: List[CheckResult] = field(default_factory=list)


def read_band_count(path: Path) -> Optional[int]:
    """Raster band count via rasterio, falling back to a Pillow mode→band map."""
    try:
        import rasterio
        with rasterio.open(path) as src:
            return int(src.count)
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("read_band_count rasterio failed for %s: %s", path.name, exc)
    try:
        from PIL import Image
        with Image.open(path) as img:
            return _MODE_BAND_COUNTS.get(img.mode, len(img.getbands()))
    except Exception as exc:
        logger.warning("read_band_count Pillow failed for %s: %s", path.name, exc)
        return None


def _rect_overlap_frac(
    before_bounds: Optional[BoundsWGS84],
    after_bounds: Optional[BoundsWGS84],
) -> Optional[float]:
    """Intersection area / after-footprint area, as a plain WGS84 rectangle."""
    if not before_bounds or not after_bounds:
        return None
    bw, bs, be, bn = before_bounds
    aw, asf, ae, an = after_bounds
    ix0, iy0 = max(bw, aw), max(bs, asf)
    ix1, iy1 = min(be, ae), min(bn, an)
    inter_w, inter_h = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter_area = inter_w * inter_h
    after_area = max(1e-12, abs(ae - aw) * abs(an - asf))
    return inter_area / after_area


def _apply_roi_to_bounds(
    bounds: Optional[BoundsWGS84], roi: Optional[dict],
) -> Optional[BoundsWGS84]:
    """Crop a WGS84 rectangle to a fractional {x,y,w,h} ROI window.

    Mirrors ``load_rgb_roi``'s convention: x/y are the top-left corner and
    y grows downward in the image's own pixel grid, i.e. y=0 is the image's
    top edge (north when north-up). The same fractional window is applied to
    both images by the caller (matching how detection itself crops both
    images to the same fractional ROI), so overlap is checked on the actual
    selected area rather than the full footprint.
    """
    if not bounds or not roi:
        return bounds
    west, south, east, north = bounds
    x, y, w, h = roi["x"], roi["y"], roi["w"], roi["h"]
    roi_west = west + x * (east - west)
    roi_east = west + (x + w) * (east - west)
    roi_north = north - y * (north - south)
    roi_south = north - (y + h) * (north - south)
    return (roi_west, roi_south, roi_east, roi_north)


def read_band_descriptions(path: Path) -> Optional[List[str]]:
    """First-3-band GDAL band descriptions, when the file has any set."""
    try:
        import rasterio
        with rasterio.open(path) as src:
            return list(src.descriptions[:3])
    except Exception:
        return None


def _suspicious_band_order(descriptions: Optional[List[Optional[str]]]) -> bool:
    """Best-effort hint only — most files carry no band descriptions at all,
    so this stays silent far more often than it fires. There is no reliable
    way to verify true RGB band *order* from metadata alone; this only flags
    the case where a sensor explicitly labelled a leading band as non-visible
    (e.g. NIR), which is worth a second look before trusting on-screen color.
    """
    if not descriptions:
        return False
    for d in descriptions:
        if not d:
            continue
        low = d.lower()
        if any(kw in low for kw in _NON_RGB_BAND_KEYWORDS):
            return True
    return False


def run_preflight_checks(
    db: Session,
    before_path: Path,
    after_path: Path,
    before_rel: str,
    after_rel: str,
    roi: Optional[dict] = None,
) -> PreflightResult:
    """Validate a (before, after) library image pair before decoding pixels.

    ``roi`` (optional fractional ``{x,y,w,h}``) narrows the overlap check to
    the actually-selected crop window instead of the full image footprints,
    matching what detection itself will run on when a ROI is set.
    """
    result = PreflightResult()

    # 1. Readability
    before_meta = inspect_image(before_path)
    after_meta = inspect_image(after_path)
    if before_meta.width <= 0 or before_meta.height <= 0:
        result.hard_fail = True
        result.fail_reason = f"Before image could not be read: {before_path.name}"
        result.checks.append(CheckResult("readability", "fail", result.fail_reason))
        return result
    if after_meta.width <= 0 or after_meta.height <= 0:
        result.hard_fail = True
        result.fail_reason = f"After image could not be read: {after_path.name}"
        result.checks.append(CheckResult("readability", "fail", result.fail_reason))
        return result
    result.checks.append(CheckResult("readability", "pass"))

    # 2. RGB bands
    before_bands = read_band_count(before_path)
    after_bands = read_band_count(after_path)
    for label, bands, path in (
        ("Before", before_bands, before_path),
        ("After", after_bands, after_path),
    ):
        if not bands:
            result.hard_fail = True
            result.fail_reason = f"{label} image band layout could not be determined: {path.name}"
            result.checks.append(CheckResult("bands", "fail", result.fail_reason))
            return result
        if bands == 2:
            result.hard_fail = True
            result.fail_reason = f"{label} image has an unsupported 2-band layout: {path.name}"
            result.checks.append(CheckResult("bands", "fail", result.fail_reason))
            return result
    band_msgs = []
    if before_bands == 1 or after_bands == 1:
        band_msgs.append(
            "One or both images are single-band (grayscale) — detection runs on a tripled grayscale channel.")
    extra_band_labels = [
        label for label, bands in (("Before", before_bands), ("After", after_bands)) if bands > 3
    ]
    if extra_band_labels:
        band_msgs.append(
            f"{' and '.join(extra_band_labels)} image has more than 3 bands — "
            "only the first 3 are used for detection.")
    suspicious_labels = [
        label for label, path in (("Before", before_path), ("After", after_path))
        if _suspicious_band_order(read_band_descriptions(path))
    ]
    if suspicious_labels:
        band_msgs.append(
            f"{' and '.join(suspicious_labels)} image band descriptions suggest non-RGB content "
            "(e.g. NIR/infrared) in the first bands — verify band order before relying on colors.")
    if band_msgs:
        for msg in band_msgs:
            result.warnings.append(msg)
            result.checks.append(CheckResult("bands", "warn", msg))
    else:
        result.checks.append(CheckResult("bands", "pass"))

    # 3. Georeferencing (also feeds the CRS/GSD/overlap checks below)
    before_geo = resolve_geo_context(db, before_rel, before_path)
    after_geo = resolve_geo_context(db, after_rel, after_path)

    if before_geo.source == "none" and after_geo.source == "none":
        # Plain, non-georeferenced photo pair — an already-supported use case
        # (pair_align's resize_only fallback). Skip CRS/GSD/overlap entirely
        # rather than penalize a mode the app deliberately allows.
        result.checks.append(CheckResult(
            "georef", "pass", "Neither image is georeferenced — treated as a plain photo pair."))
        result.checks.append(CheckResult("crs", "pass"))
        result.checks.append(CheckResult("gsd", "pass"))
        # With no georeferencing, overlap can't be checked geographically — but
        # detection still force-resizes "after" to "before"'s pixel dimensions
        # regardless, so a large aspect-ratio gap is worth flagging here instead.
        ratio_b = before_meta.width / before_meta.height if before_meta.height else 0.0
        ratio_a = after_meta.width / after_meta.height if after_meta.height else 0.0
        if ratio_b > 0 and ratio_a > 0:
            rel_diff = abs(ratio_b - ratio_a) / max(ratio_b, ratio_a)
            if rel_diff > get_max_aspect_ratio_diff():
                msg = (
                    f"Images have different aspect ratios (before {before_meta.width}x{before_meta.height}, "
                    f"after {after_meta.width}x{after_meta.height}) — the after image will be stretched "
                    "to match, which may distort content.")
                result.warnings.append(msg)
                result.checks.append(CheckResult("aspect_ratio", "warn", msg))
            else:
                result.checks.append(CheckResult("aspect_ratio", "pass"))
        result.checks.append(CheckResult("overlap", "pass"))
        return result

    if before_geo.source == "none" or after_geo.source == "none":
        msg = "Only one image is georeferenced — geographic overlap could not be verified."
        result.warnings.append(msg)
        result.checks.append(CheckResult("georef", "warn", msg))
    elif before_geo.source == "manual" or after_geo.source == "manual":
        msg = "One or both images rely on manually-entered bounds rather than embedded georeferencing."
        result.warnings.append(msg)
        result.checks.append(CheckResult("georef", "warn", msg))
    else:
        result.checks.append(CheckResult("georef", "pass"))

    # 4. CRS — present but unreprojectable is distinguishable from absent
    before_georef = read_georef(before_path)
    after_georef = read_georef(after_path)
    broken_crs = [
        label for label, gi in (("Before", before_georef), ("After", after_georef))
        if gi is not None and gi.crs is not None and gi.bounds_wgs84 is None
    ]
    if broken_crs:
        msg = (f"{' and '.join(broken_crs)} image CRS is present but could not be reprojected "
               "to WGS84 — verify the coordinate system.")
        result.warnings.append(msg)
        result.checks.append(CheckResult("crs", "warn", msg))
    else:
        result.checks.append(CheckResult("crs", "pass"))

    # 5. GSD — never a hard fail, only harmonization is affected downstream
    gsd_b = read_gsd_meters(before_path)
    gsd_a = read_gsd_meters(after_path)
    if gsd_b and gsd_a:
        rel_diff = abs(gsd_b - gsd_a) / max(gsd_b, gsd_a)
        tolerance = get_gsd_tolerance()
        if rel_diff > tolerance:
            msg = (f"Ground sample distance differs by {rel_diff * 100:.0f}% "
                   f"(before {gsd_b:.2f} m/px, after {gsd_a:.2f} m/px) — "
                   "the finer image will be resampled to match.")
            result.warnings.append(msg)
            result.checks.append(CheckResult("gsd", "warn", msg))
        else:
            result.checks.append(CheckResult("gsd", "pass"))
    else:
        result.checks.append(CheckResult("gsd", "pass"))

    # 6. Overlap — cropped to the selected ROI when one is set, so the check
    # reflects the area detection will actually run on, not the full images.
    before_bounds = _apply_roi_to_bounds(before_geo.bounds, roi)
    after_bounds = _apply_roi_to_bounds(after_geo.bounds, roi)
    roi_note = " within the selected area" if roi else ""
    overlap_frac = _rect_overlap_frac(before_bounds, after_bounds)
    if overlap_frac is not None:
        hard_min = get_min_overlap_hard()
        warn_min = get_min_overlap_warn()
        if overlap_frac < hard_min:
            result.hard_fail = True
            result.fail_reason = (
                f"Images do not overlap on the ground{roi_note} (overlap {overlap_frac * 100:.1f}% "
                "of the after-image footprint) — not suitable for change detection.")
            result.checks.append(CheckResult("overlap", "fail", result.fail_reason))
            return result
        if overlap_frac < warn_min:
            msg = f"Weak geographic overlap{roi_note} ({overlap_frac * 100:.1f}% of the after-image footprint)."
            result.warnings.append(msg)
            result.checks.append(CheckResult("overlap", "warn", msg))
        else:
            result.checks.append(CheckResult("overlap", "pass"))
    else:
        result.checks.append(CheckResult("overlap", "pass"))

    return result
