"""XYZ map tiles for georeferenced library rasters (QGIS-style overlay)."""
from __future__ import annotations

import hashlib
import io
import logging
import math
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import quote

from PIL import Image

from .config import LOCAL_THUMB_CACHE
from .geotiff_io import inspect_image, read_georef
from .tree.path_service import resolve_file

logger = logging.getLogger(__name__)

TILE_SIZE = 256
MAX_ZOOM = 22
WEB_MERCATOR_ORIGIN = 20037508.342789244
_EMPTY_PNG: Optional[bytes] = None

BoundsWGS84 = Tuple[float, float, float, float]


def empty_tile_png() -> bytes:
    global _EMPTY_PNG
    if _EMPTY_PNG is None:
        buf = io.BytesIO()
        Image.new("RGBA", (TILE_SIZE, TILE_SIZE), (0, 0, 0, 0)).save(buf, format="PNG")
        _EMPTY_PNG = buf.getvalue()
    return _EMPTY_PNG


def xyz_bounds_3857(z: int, x: int, y: int) -> Tuple[float, float, float, float]:
    """Web Mercator bounds of an XYZ tile (minx, miny, maxx, maxy)."""
    n = 2 ** int(z)
    tile_w = (2.0 * WEB_MERCATOR_ORIGIN) / n
    minx = -WEB_MERCATOR_ORIGIN + x * tile_w
    maxx = -WEB_MERCATOR_ORIGIN + (x + 1) * tile_w
    maxy = WEB_MERCATOR_ORIGIN - y * tile_w
    miny = WEB_MERCATOR_ORIGIN - (y + 1) * tile_w
    return minx, miny, maxx, maxy


def _rects_intersect(a: Tuple[float, float, float, float],
                     b: Tuple[float, float, float, float]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def _max_native_zoom(bounds: Optional[BoundsWGS84], width: int, height: int) -> int:
    """Leaflet maxNativeZoom from raster GSD (approx, WGS84)."""
    if not bounds or width <= 0 or height <= 0:
        return 18
    west, south, east, north = bounds
    lat = max(-85.0, min(85.0, (south + north) / 2.0))
    # metres per pixel along the longer raster side
    m_per_deg_lat = 111_320.0
    m_per_deg_lng = 111_320.0 * max(0.05, math.cos(math.radians(lat)))
    gsd = min(
        abs(east - west) * m_per_deg_lng / max(width, 1),
        abs(north - south) * m_per_deg_lat / max(height, 1),
    )
    gsd = max(gsd, 0.02)
    mpp_z0 = 156543.03392804097 * math.cos(math.radians(lat))
    z = int(round(math.log2(max(mpp_z0 / gsd, 1.0))))
    return max(12, min(MAX_ZOOM, z))


def _bounds_dict(bounds: Optional[BoundsWGS84]) -> Optional[dict]:
    if not bounds:
        return None
    west, south, east, north = bounds
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
        "latLng": [[south, west], [north, east]],
    }


def build_map_info(relative_path: str, db=None) -> dict:
    """Metadata the browser needs to overlay a library raster on XYZ basemaps."""
    rel = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    full = resolve_file(rel)
    meta = inspect_image(full)
    georef = read_georef(full)
    bounds = meta.bounds_wgs84
    source = meta.georef_source or "none"
    can_tile = bool(georef and georef.crs and bounds)

    if not bounds and db is not None:
        from .geo_regions import parse_bounds
        from .tree.image_service import get_image_by_file_path

        img = get_image_by_file_path(db, rel)
        if img and img.bounds_json:
            parsed = parse_bounds(img.bounds_json)
            if parsed:
                bounds = parsed
                source = "manual"
                can_tile = bool(georef and georef.crs)

    encoded = quote(rel, safe="/")
    merc_bounds = _mercator_aligned_wgs84(full) if can_tile else None
    overlay_bounds = merc_bounds or bounds
    return {
        "path": rel,
        "filename": Path(rel).name,
        "hasGeoref": bool(overlay_bounds),
        "canTile": can_tile,
        "georefSource": source if overlay_bounds else "none",
        "crs": str(georef.crs) if (georef and georef.crs) else (meta.crs or ""),
        "bounds": _bounds_dict(overlay_bounds),
        "width": meta.width,
        "height": meta.height,
        "maxNativeZoom": _max_native_zoom(overlay_bounds, meta.width, meta.height),
        "tileUrl": f"/api/dda/local/map-tiles/{{z}}/{{x}}/{{y}}.png?path={encoded}",
        "overviewUrl": (
            f"/api/dda/local/map-overview?path={encoded}&max=2048" if can_tile else None
        ),
        "previewUrl": f"/api/dda/local/preview?path={encoded}&max=2048",
    }


def _tile_cache_path(relative_path: str, src: Path, z: int, x: int, y: int) -> Path:
    key = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:24]
    mtime = int(src.stat().st_mtime)
    return (
        LOCAL_THUMB_CACHE
        / "map-tiles"
        / f"{key}_{mtime}"
        / str(z)
        / str(x)
        / f"{y}.png"
    )


def _to_rgba(data, mask) -> Image.Image:
    import numpy as np

    count = int(data.shape[0])
    if count == 1:
        rgb = np.stack([data[0], data[0], data[0]])
    else:
        rgb = data[:3]
    rgb = np.transpose(rgb, (1, 2, 0)).astype("float32")
    if data.dtype != np.uint8 or float(np.nanmax(rgb) if rgb.size else 0) > 255:
        lo, hi = np.percentile(rgb, (2, 98)) if rgb.size else (0, 1)
        rgb = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) * 255
    rgb_u8 = np.clip(rgb, 0, 255).astype("uint8")
    if mask is None:
        alpha = np.full(rgb_u8.shape[:2], 255, dtype="uint8")
    else:
        alpha = np.asarray(mask)
        if alpha.ndim == 3:
            alpha = alpha[0]
        if alpha.shape != rgb_u8.shape[:2]:
            alpha = np.full(rgb_u8.shape[:2], 255, dtype="uint8")
        else:
            alpha = np.where(alpha > 0, 255, 0).astype("uint8")
    rgba = np.dstack([rgb_u8, alpha])
    return Image.fromarray(rgba, mode="RGBA")


def render_xyz_tile(relative_path: str, z: int, x: int, y: int) -> bytes:
    """Warp one Web-Mercator XYZ tile from a georeferenced raster."""
    if z < 0 or z > MAX_ZOOM:
        return empty_tile_png()
    n = 2 ** z
    if x < 0 or y < 0 or x >= n or y >= n:
        return empty_tile_png()

    rel = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    src_path = resolve_file(rel)
    cache = _tile_cache_path(rel, src_path, z, x, y)
    try:
        if cache.exists() and cache.stat().st_mtime >= src_path.stat().st_mtime:
            return cache.read_bytes()
    except OSError:
        pass

    png = _render_tile_uncached(src_path, z, x, y)
    if png == empty_tile_png():
        return png
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp.png")
        tmp.write_bytes(png)
        tmp.replace(cache)
    except OSError as exc:
        logger.debug("Map tile cache write skipped: %s", exc)
    return png


def _mercator_aligned_wgs84(src_path: Path) -> Optional[BoundsWGS84]:
    """WGS84 box of the raster's Web-Mercator envelope (matches Google XYZ tiles)."""
    try:
        import rasterio
        from rasterio.crs import CRS
        from rasterio.warp import transform_bounds
    except ImportError:
        return None
    try:
        with rasterio.open(src_path) as src:
            if src.crs is None:
                return None
            web = CRS.from_epsg(3857)
            wgs = CRS.from_epsg(4326)
            crs = _crs_2d(src.crs)
            minx, miny, maxx, maxy = transform_bounds(crs, web, *src.bounds, densify_pts=21)
            west, south, east, north = transform_bounds(web, wgs, minx, miny, maxx, maxy)
            return (float(west), float(south), float(east), float(north))
    except Exception as exc:
        logger.debug("mercator-aligned bounds failed for %s: %s", src_path.name, exc)
        return None


def render_mercator_overview(relative_path: str, max_side: int = 2048) -> bytes:
    """North-up Web-Mercator PNG so Leaflet ImageOverlay matches Google tiles."""
    rel = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    src_path = resolve_file(rel)
    max_side = max(256, min(int(max_side), 4096))
    key = hashlib.sha256(f"{rel}:overview:{max_side}".encode("utf-8")).hexdigest()[:24]
    mtime = int(src_path.stat().st_mtime)
    cache = LOCAL_THUMB_CACHE / "map-overview" / f"{key}_{mtime}_{max_side}.png"
    try:
        if cache.exists() and cache.stat().st_mtime >= src_path.stat().st_mtime:
            return cache.read_bytes()
    except OSError:
        pass
    png = _render_mercator_overview_uncached(src_path, max_side)
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(".tmp.png")
        tmp.write_bytes(png)
        tmp.replace(cache)
    except OSError as exc:
        logger.debug("Overview cache write skipped: %s", exc)
    return png


def _render_mercator_overview_uncached(src_path: Path, max_side: int) -> bytes:
    import numpy as np
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds as affine_from_bounds
    from rasterio.warp import reproject, transform_bounds

    web = CRS.from_epsg(3857)
    with rasterio.open(src_path) as src:
        if src.crs is None:
            raise ValueError("raster has no CRS")
        src_crs = _crs_2d(src.crs)
        minx, miny, maxx, maxy = transform_bounds(src_crs, web, *src.bounds, densify_pts=21)
        width_m = max(maxx - minx, 1e-6)
        height_m = max(maxy - miny, 1e-6)
        if width_m >= height_m:
            out_w = max_side
            out_h = max(1, int(round(max_side * height_m / width_m)))
        else:
            out_h = max_side
            out_w = max(1, int(round(max_side * width_m / height_m)))
        count = min(3, src.count)
        indexes = list(range(1, count + 1))
        dst_transform = affine_from_bounds(minx, miny, maxx, maxy, out_w, out_h)
        src_nodata = src.nodata
        if src_nodata is not None and 0 <= float(src_nodata) <= 255:
            src_nodata = None
        sentinel = -1.0
        dst = np.full((count, out_h, out_w), sentinel, dtype=np.float32)
        reproject(
            source=rasterio.band(src, indexes),
            destination=dst,
            src_transform=src.transform,
            src_crs=src_crs,
            dst_transform=dst_transform,
            dst_crs=web,
            resampling=Resampling.bilinear,
            src_nodata=src_nodata,
            dst_nodata=sentinel,
        )
        valid = dst[0] != sentinel
        if not np.any(valid):
            return empty_tile_png()
        rgb = dst.copy()
        rgb[:, ~valid] = 0
        mask = np.where(valid, 255, 0).astype(np.uint8)
    img = _to_rgba(rgb, mask)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=False)
    return buf.getvalue()


def _crs_2d(crs):
    try:
        if hasattr(crs, "to_2d"):
            two = crs.to_2d()
            if two:
                return two
    except Exception:
        pass
    return crs


def _render_tile_uncached(src_path: Path, z: int, x: int, y: int) -> bytes:
    try:
        import numpy as np
        import rasterio
        from rasterio.crs import CRS
        from rasterio.enums import Resampling
        from rasterio.transform import from_bounds as affine_from_bounds
        from rasterio.warp import reproject, transform_bounds
    except ImportError:
        logger.warning("rasterio missing — cannot render map tiles")
        return empty_tile_png()

    minx, miny, maxx, maxy = xyz_bounds_3857(z, x, y)
    web_merc = CRS.from_epsg(3857)

    try:
        with rasterio.open(src_path) as src:
            if src.crs is None:
                return empty_tile_png()
            src_crs = _crs_2d(src.crs)
            try:
                src_3857 = transform_bounds(src_crs, web_merc, *src.bounds, densify_pts=21)
            except Exception:
                try:
                    src_3857 = transform_bounds(src.crs, web_merc, *src.bounds, densify_pts=21)
                    src_crs = src.crs
                except Exception:
                    return empty_tile_png()
            if not _rects_intersect(src_3857, (minx, miny, maxx, maxy)):
                return empty_tile_png()

            count = min(3, src.count)
            indexes = list(range(1, count + 1))
            dst_transform = affine_from_bounds(minx, miny, maxx, maxy, TILE_SIZE, TILE_SIZE)
            # RGB 0 is valid (shadows/black roofs). Only treat a true nodata
            # that is outside 0–255, otherwise GDAL punches holes in the overlay.
            src_nodata = src.nodata
            if src_nodata is not None and 0 <= float(src_nodata) <= 255:
                src_nodata = None
            sentinel = -1.0
            dst = np.full((count, TILE_SIZE, TILE_SIZE), sentinel, dtype=np.float32)
            reproject(
                source=rasterio.band(src, indexes),
                destination=dst,
                src_transform=src.transform,
                src_crs=src_crs,
                dst_transform=dst_transform,
                dst_crs=web_merc,
                resampling=Resampling.bilinear,
                src_nodata=src_nodata,
                dst_nodata=sentinel,
            )
            valid = dst[0] != sentinel
            if not np.any(valid):
                return empty_tile_png()
            rgb = dst.copy()
            rgb[:, ~valid] = 0
            mask = np.where(valid, 255, 0).astype(np.uint8)
        img = _to_rgba(rgb, mask)
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=False)
        return buf.getvalue()
    except Exception as exc:
        logger.warning("Map tile %s z=%s x=%s y=%s failed: %s", src_path.name, z, x, y, exc)
        return empty_tile_png()


BASEMAP_PRESETS: list[dict[str, Any]] = [
    {
        "id": "osm",
        "label": "OpenStreetMap",
        "url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        "attribution": "&copy; OpenStreetMap contributors",
        "maxZoom": 19,
        "subdomains": "",
    },
    {
        "id": "google-satellite",
        "label": "Google Satellite",
        "url": "https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
        "attribution": "Imagery &copy; Google",
        "maxZoom": 21,
        "subdomains": "0123",
    },
    {
        "id": "google-hybrid",
        "label": "Google Hybrid",
        "url": "https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
        "attribution": "Imagery &copy; Google",
        "maxZoom": 21,
        "subdomains": "0123",
    },
    {
        "id": "esri-imagery",
        "label": "Esri World Imagery",
        "url": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        "attribution": "Tiles &copy; Esri",
        "maxZoom": 19,
        "subdomains": "",
    },
    {
        "id": "custom-xyz",
        "label": "Custom XYZ…",
        "url": "",
        "attribution": "",
        "maxZoom": 22,
        "subdomains": "",
    },
]
