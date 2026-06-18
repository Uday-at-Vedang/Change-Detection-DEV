"""Pixel coordinates → WGS84 lat/lng for geo-referenced images (FR-04, FR-06)."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .change_type_map import enrich_region_for_dda
from .geotiff_io import inspect_image

logger = logging.getLogger(__name__)

BoundsWGS84 = Tuple[float, float, float, float]  # west, south, east, north


def parse_bounds(bounds: Any) -> Optional[BoundsWGS84]:
    if bounds is None:
        return None
    if isinstance(bounds, (list, tuple)) and len(bounds) == 4:
        return tuple(float(x) for x in bounds)
    if isinstance(bounds, dict):
        try:
            return (
                float(bounds["west"]),
                float(bounds["south"]),
                float(bounds["east"]),
                float(bounds["north"]),
            )
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(bounds, str) and bounds.strip():
        try:
            data = json.loads(bounds)
            return parse_bounds(data)
        except json.JSONDecodeError:
            parts = [float(x.strip()) for x in bounds.replace("[", "").replace("]", "").split(",")]
            if len(parts) == 4:
                return tuple(parts)
    return None


def bounds_from_image_path(path: Path) -> Optional[BoundsWGS84]:
    try:
        meta = inspect_image(path)
        return meta.bounds_wgs84
    except Exception as exc:
        logger.warning("Could not read bounds for %s: %s", path.name, exc)
        return None


def pixel_to_lat_lng(
    x: float,
    y: float,
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
) -> Optional[Dict[str, float]]:
    if img_width <= 0 or img_height <= 0 or not bounds:
        return None
    west, south, east, north = bounds
    lng = west + (float(x) / img_width) * (east - west)
    lat = north - (float(y) / img_height) * (north - south)
    return {"lat": round(lat, 6), "lng": round(lng, 6)}


def bbox_area_sq_m(
    bbox: Dict[str, int],
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
) -> Optional[float]:
    """Approximate region area in square metres using geographic bounds."""
    if img_width <= 0 or img_height <= 0 or not bounds:
        return None
    west, south, east, north = bounds
    m_per_px_x = abs(east - west) / img_width
    m_per_px_y = abs(north - south) / img_height
    # Rough conversion: 1 degree ≈ 111_320 m at equator; scale lng by cos(lat)
    import math
    mid_lat = (north + south) / 2.0
    lat_scale = 111_320.0
    lng_scale = 111_320.0 * math.cos(math.radians(mid_lat))
    w_m = bbox.get("w", 0) * m_per_px_x * lng_scale
    h_m = bbox.get("h", 0) * m_per_px_y * lat_scale
    return round(w_m * h_m, 1)


def enrich_regions_geo(
    regions: List[dict],
    *,
    img_width: int,
    img_height: int,
    bounds: Optional[BoundsWGS84],
) -> List[dict]:
    """Add latLng, areaSqM, and DDA change type to each region."""
    out = []
    for region in regions:
        enriched = enrich_region_for_dda(region)
        center = region.get("center") or {}
        cx = center.get("x", 0)
        cy = center.get("y", 0)
        if bounds:
            lat_lng = pixel_to_lat_lng(cx, cy, img_width, img_height, bounds)
            if lat_lng:
                enriched["latLng"] = lat_lng
            bbox = region.get("bbox") or {}
            area_sq_m = bbox_area_sq_m(bbox, img_width, img_height, bounds)
            if area_sq_m is not None:
                enriched["areaSqM"] = area_sq_m
        else:
            enriched["latLng"] = None
        out.append(enriched)
    return out


def region_lat_lng(region: dict) -> tuple[Optional[float], Optional[float]]:
    """Read lat/lng from region dict (supports latLng object or flat keys)."""
    ll = region.get("latLng") or {}
    lat = region.get("latitude", ll.get("lat") if isinstance(ll, dict) else None)
    lng = region.get("longitude", ll.get("lng") if isinstance(ll, dict) else None)
    if lat is None or lng is None:
        return None, None
    try:
        return float(lat), float(lng)
    except (TypeError, ValueError):
        return None, None

