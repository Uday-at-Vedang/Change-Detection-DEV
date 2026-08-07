"""Pixel coordinates â†’ WGS84 lat/lng for geo-referenced images (FR-04, FR-06)."""
from __future__ import annotations

import json
import logging
import math
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
    source: str = "none"  # embedded | worldfile | manual | linear | none


def _bounds_in_range(b: Optional[BoundsWGS84]) -> bool:
    if not b:
        return False
    w, s, e, n = b
    return abs(w) <= 180 and abs(e) <= 180 and abs(s) <= 90 and abs(n) <= 90


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
    """Resolve bounds and affine georef for detection geo enrichment.

    Priority: embedded/world-file affine georef â†’ DB manual bounds â†’ linear
    bounds inferred from the image sidecars. Tracks which source was used.
    """
    georef = read_georef(base_file)
    bounds = georef.bounds_wgs84 if georef else None
    georef_width = georef.width if georef else 0
    georef_height = georef.height if georef else 0
    source = getattr(georef, "source", "embedded") if georef else "none"

    if not bounds:
        from .tree.image_service import get_image_by_file_path

        rel = base_path.replace("\\", "/").strip().lstrip("/")
        img = get_image_by_file_path(db, rel)
        if img and img.bounds_json:
            db_bounds = parse_bounds(img.bounds_json)
            if db_bounds:
                bounds = db_bounds
                source = "manual"

    if not bounds:
        bounds = bounds_from_image_path(base_file)
        if bounds:
            source = "linear"

    if (georef_width <= 0 or georef_height <= 0) and bounds:
        meta = inspect_image(base_file)
        georef_width = meta.width or georef_width
        georef_height = meta.height or georef_height

    return GeoContext(
        bounds=bounds,
        georef=georef,
        georef_width=georef_width or 0,
        georef_height=georef_height or 0,
        source=source if bounds else "none",
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
            # Guard against bad transforms emitting impossible coordinates
            if abs(lat) <= 90 and abs(lng) <= 180:
                return {"lat": round(lat, 6), "lng": round(lng, 6)}
            logger.warning("Affine produced out-of-range lat/lng (%.3f,%.3f); using linear", lat, lng)

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


def _ground_m_per_px(
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[Tuple[float, float]]:
    """Metres per detection-image pixel along X (east) and Y (south)."""
    if img_width <= 0 or img_height <= 0 or not bounds:
        return None
    west, south, east, north = bounds
    ref_w = geo.georef_width if geo and geo.georef_width > 0 else img_width
    ref_h = geo.georef_height if geo and geo.georef_height > 0 else img_height
    if ref_w <= 0 or ref_h <= 0:
        return None
    scale_x = ref_w / float(img_width)
    scale_y = ref_h / float(img_height)
    mid_lat = (north + south) / 2.0
    lat_scale = 111_320.0
    lng_scale = 111_320.0 * math.cos(math.radians(mid_lat))
    m_per_px_x = abs(east - west) / ref_w * scale_x * lng_scale
    m_per_px_y = abs(north - south) / ref_h * scale_y * lat_scale
    if m_per_px_x <= 0 or m_per_px_y <= 0:
        return None
    return m_per_px_x, m_per_px_y


def bbox_area_sq_m(
    bbox: Dict[str, int],
    img_width: int,
    img_height: int,
    bounds: BoundsWGS84,
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[float]:
    """Approximate region area in square metres using the axis-aligned bbox."""
    scales = _ground_m_per_px(img_width, img_height, bounds, geo=geo)
    if not scales:
        return None
    m_per_px_x, m_per_px_y = scales
    w_m = float(bbox.get("w", 0)) * m_per_px_x
    h_m = float(bbox.get("h", 0)) * m_per_px_y
    return round(w_m * h_m, 1)


def polygon_area_px(polygon: Optional[List]) -> Optional[float]:
    """Shoelace area of an image-pixel ring (absolute value, closed or open)."""
    if not polygon or len(polygon) < 3:
        return None
    try:
        pts = [(float(p[0]), float(p[1])) for p in polygon]
    except (TypeError, ValueError, IndexError):
        return None
    if pts[0] == pts[-1]:
        pts = pts[:-1]
    if len(pts) < 3:
        return None
    area2 = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
    return abs(area2) * 0.5


def polygon_area_sq_m(
    polygon: Optional[List],
    img_width: int,
    img_height: int,
    bounds: Optional[BoundsWGS84],
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[float]:
    """Ground area of a pixel-space polygon footprint (preferred over bbox)."""
    if not bounds:
        return None
    scales = _ground_m_per_px(img_width, img_height, bounds, geo=geo)
    if not scales:
        return None
    m_per_px_x, m_per_px_y = scales
    px_area = polygon_area_px(polygon)
    if px_area is None:
        return None
    return round(px_area * m_per_px_x * m_per_px_y, 1)


def polygon_to_lat_lng(
    polygon: List[List[float]],
    img_width: int,
    img_height: int,
    bounds: Optional[BoundsWGS84],
    *,
    geo: Optional[GeoContext] = None,
) -> Optional[List[Dict[str, float]]]:
    """Project a pixel-space polygon ring to WGS84, vertex by vertex.

    Uses the same transform as the bbox-centre ``latLng`` so a polygon and its
    region always agree. Returns None when any vertex fails to project, since a
    partially-projected ring would draw a wrong footprint on a map.
    """
    if not polygon:
        return None
    ring: List[Dict[str, float]] = []
    for point in polygon:
        try:
            x, y = float(point[0]), float(point[1])
        except (TypeError, ValueError, IndexError):
            return None
        coords = pixel_to_lat_lng(x, y, img_width, img_height, bounds, geo=geo)
        if not coords:
            return None
        ring.append(coords)
    return ring or None


def enrich_regions_geo(
    regions: List[dict],
    *,
    img_width: int,
    img_height: int,
    bounds: Optional[BoundsWGS84],
    geo: Optional[GeoContext] = None,
) -> List[dict]:
    """Add latLng, polygonGeo, areaSqM, and DDA change type to each region."""
    effective_bounds = bounds or (geo.bounds if geo else None)
    out = []
    for region in regions:
        enriched = enrich_region_for_dda(region)
        center = region.get("center") or {}
        cx = center.get("x", 0)
        cy = center.get("y", 0)
        poly = region.get("polygon")
        poly_px = polygon_area_px(poly)
        if poly_px is not None:
            enriched["polygonAreaPx"] = round(poly_px, 1)
        if effective_bounds or (geo and geo.georef):
            lat_lng = pixel_to_lat_lng(
                cx, cy, img_width, img_height, effective_bounds,
                geo=geo,
            )
            if lat_lng:
                enriched["latLng"] = lat_lng
            polygon_geo = polygon_to_lat_lng(
                poly, img_width, img_height, effective_bounds,
                geo=geo,
            )
            if polygon_geo:
                enriched["polygonGeo"] = polygon_geo
            bbox = region.get("bbox") or {}
            if effective_bounds:
                # Prefer true polygon footprint area; bbox is the fallback.
                area_sq_m = polygon_area_sq_m(
                    poly, img_width, img_height, effective_bounds, geo=geo,
                )
                if area_sq_m is None:
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
