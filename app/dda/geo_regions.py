"""Pixel coordinates → WGS84 lat/lng for geo-referenced images (FR-04, FR-06)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from .change_type_map import enrich_region_for_dda
from .geotiff_io import GeorefInfo, inspect_image, pixel_to_geo_wgs84, read_georef

logger = logging.getLogger(__name__)

BoundsWGS84 = Tuple[float, float, float, float]  # west, south, east, north


@dataclass
class GeoContext:
    bounds: Optional[BoundsWGS84]
    georef: Optional[GeorefInfo]
    georef_width: int
    georef_height: int


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


def resolve_geo_context(
    db: Session,
    base_path: str,
    base_file: Path,
) -> GeoContext:
    """Resolve bounds and affine georef for detection geo enrichment."""
    georef = read_georef(base_file)
    bounds = georef.bounds_wgs84 if georef else None
    georef_width = georef.width if georef else 0
    georef_height = georef.height if georef else 0

    if not bounds:
        from .tree.image_service import get_image_by_file_path

        rel = base_path.replace("\\", "/").strip().lstrip("/")
        img = get_image_by_file_path(db, rel)
        if img and img.bounds_json:
            bounds = parse_bounds(img.bounds_json)

    if not bounds:
        bounds = bounds_from_image_path(base_file)

    if georef is None and bounds:
        meta = inspect_image(base_file)
        georef_width = meta.width or georef_width
        georef_height = meta.height or georef_height

    return GeoContext(
        bounds=bounds,
        georef=georef,
        georef_width=georef_width or 0,
        georef_height=georef_height or 0,
    )


def pixel_to_lat_lng(
    x: float,
    y: float,
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[Dict[str, float]]:
    if img_width <= 0 or img_height <= 0:
        return None

    if geo and geo.georef:
        coords = pixel_to_geo_wgs84(
            x, y, geo.georef,
            detection_width=img_width,
            detection_height=img_height,
        )
        if coords:
            lng, lat = coords
            return {"lat": round(lat, 6), "lng": round(lng, 6)}

    if not bounds:
        return None

    west, south, east, north = bounds
    ref_w = geo.georef_width if geo and geo.georef_width > 0 else img_width
    ref_h = geo.georef_height if geo and geo.georef_height > 0 else img_height
    scale_x = ref_w / float(img_width)
    scale_y = ref_h / float(img_height)
    px = float(x) * scale_x
    py = float(y) * scale_y
    lng = west + (px / ref_w) * (east - west)
    lat = north - (py / ref_h) * (north - south)
    return {"lat": round(lat, 6), "lng": round(lng, 6)}


def bbox_area_sq_m(
    bbox: Dict[str, int],
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[float]:
    """Approximate region area in square metres using geographic bounds."""
    if img_width <= 0 or img_height <= 0 or not bounds:
        return None
    west, south, east, north = bounds
    ref_w = geo.georef_width if geo and geo.georef_width > 0 else img_width
    ref_h = geo.georef_height if geo and geo.georef_height > 0 else img_height
    scale_x = ref_w / float(img_width)
    scale_y = ref_h / float(img_height)
    m_per_px_x = abs(east - west) / ref_w
    m_per_px_y = abs(north - south) / ref_h
    import math
    mid_lat = (north + south) / 2.0
    lat_scale = 111_320.0
    lng_scale = 111_320.0 * math.cos(math.radians(mid_lat))
    w_m = bbox.get("w", 0) * scale_x * m_per_px_x * lng_scale
    h_m = bbox.get("h", 0) * scale_y * m_per_px_y * lat_scale
    return round(w_m * h_m, 1)


def enrich_regions_geo(
    regions: List[dict],
    *,
    img_width: int,
    img_height: int,
    bounds: Optional[BoundsWGS84],
    geo: Optional[GeoContext] = None,
) -> List[dict]:
    """Add latLng, areaSqM, and DDA change type to each region."""
    effective_bounds = bounds or (geo.bounds if geo else None)
    out = []
    for region in regions:
        enriched = enrich_region_for_dda(region)
        center = region.get("center") or {}
        cx = center.get("x", 0)
        cy = center.get("y", 0)
        if effective_bounds or (geo and geo.georef):
            lat_lng = pixel_to_lat_lng(
                cx, cy, img_width, img_height, effective_bounds or (0, 0, 0, 0),
                geo=geo,
            )
            if lat_lng:
                enriched["latLng"] = lat_lng
            bbox = region.get("bbox") or {}
            if effective_bounds:
                area_sq_m = bbox_area_sq_m(
                    bbox, img_width, img_height, effective_bounds, geo=geo,
                )
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
