"""GeoTIFF ingest and preview generation (FR-02). Rasterio optional at import time."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from PIL import Image, ImageOps

from .config import get_detection_max_side

logger = logging.getLogger(__name__)

# World-file extensions per image type (ESRI convention + generic .wld)
_WORLD_FILE_EXTS = {
    ".tif": (".tfw", ".tifw", ".wld"),
    ".tiff": (".tfw", ".tifw", ".wld"),
    ".jpg": (".jgw", ".jpgw", ".wld"),
    ".jpeg": (".jgw", ".jpgw", ".wld"),
    ".png": (".pgw", ".pngw", ".wld"),
}


@dataclass
class IngestResult:
    width: int
    height: int
    has_georef: bool
    crs: str
    bounds_wgs84: Optional[Tuple[float, float, float, float]]  # west, south, east, north
    format: str
    georef_source: str = "none"  # embedded | worldfile | none


def _find_world_file(path: Path) -> Optional[Path]:
    """Locate an ESRI world file or generic .wld sidecar next to an image."""
    ext = path.suffix.lower()
    candidates = []
    for wext in _WORLD_FILE_EXTS.get(ext, (".wld",)):
        candidates.append(path.with_suffix(wext))
    # e.g. photo.jpg.wld
    candidates.append(Path(str(path) + ".wld"))
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _read_prj_crs(path: Path):
    """Read a .prj sidecar (WKT) and return a rasterio CRS, or default EPSG:4326."""
    try:
        from rasterio.crs import CRS
    except Exception:
        return None
    prj = path.with_suffix(".prj")
    if prj.is_file():
        try:
            return CRS.from_wkt(prj.read_text(encoding="utf-8", errors="ignore").strip())
        except Exception as exc:
            logger.warning("Could not parse .prj for %s: %s", path.name, exc)
    try:
        return CRS.from_epsg(4326)
    except Exception:
        return None


def _world_file_georef(path: Path, width: int, height: int):
    """Build (transform, crs, bounds_wgs84) from a world file sidecar, or None."""
    wf = _find_world_file(path)
    if wf is None:
        return None
    try:
        from rasterio.transform import Affine, array_bounds
        from rasterio.warp import transform_bounds

        nums = [float(x.strip()) for x in wf.read_text().split() if x.strip()]
        if len(nums) < 6:
            return None
        a, d, b, e, c, f = nums[:6]
        # World file stores center of top-left pixel; shift to corner for GDAL/affine
        gt_c = c - a / 2.0 - b / 2.0
        gt_f = f - d / 2.0 - e / 2.0
        transform = Affine(a, b, gt_c, d, e, gt_f)
        crs = _read_prj_crs(path)
        west, south, east, north = array_bounds(height, width, transform)
        if crs is not None and str(crs) not in ("EPSG:4326", "OGC:CRS84"):
            west, south, east, north = transform_bounds(crs, "EPSG:4326", west, south, east, north)
        return transform, crs, (float(west), float(south), float(east), float(north))
    except Exception as exc:
        logger.warning("World-file georef failed for %s: %s", path.name, exc)
        return None


def _read_with_rasterio(path: Path) -> IngestResult:
    import rasterio
    from rasterio.warp import transform_bounds

    with rasterio.open(path) as src:
        crs = str(src.crs) if src.crs else ""
        has_georef = src.crs is not None
        bounds = None
        if has_georef and src.bounds:
            try:
                w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                bounds = (float(w), float(s), float(e), float(n))
            except Exception as exc:
                logger.warning("Could not transform bounds to WGS84: %s", exc)
        ext = path.suffix.lower()
        return IngestResult(
            width=int(src.width),
            height=int(src.height),
            has_georef=has_georef,
            crs=crs,
            bounds_wgs84=bounds,
            format="geotiff" if ext in (".tif", ".tiff") else "image",
            georef_source="embedded" if has_georef else "none",
        )


def _read_with_pillow(path: Path) -> IngestResult:
    with Image.open(path) as img:
        w, h = img.size
    ext = path.suffix.lower()
    fmt = "geotiff" if ext in (".tif", ".tiff") else "image"
    # Try a world-file sidecar for plain images / non-georeferenced TIFFs
    wf = _world_file_georef(path, w, h)
    if wf is not None:
        _, crs, bounds = wf
        return IngestResult(
            width=w, height=h, has_georef=bounds is not None,
            crs=str(crs) if crs else "", bounds_wgs84=bounds,
            format=fmt, georef_source="worldfile" if bounds else "none",
        )
    return IngestResult(
        width=w,
        height=h,
        has_georef=False,
        crs="",
        bounds_wgs84=None,
        format=fmt,
        georef_source="none",
    )


def inspect_image(path: Path) -> IngestResult:
    """Read dimensions + georeferencing for any raster (TIFF/PNG/JPEG).

    Tries rasterio first (honors embedded CRS, world files and GDAL .aux.xml for
    all formats), then falls back to Pillow + explicit world-file parsing.
    """
    try:
        res = _read_with_rasterio(path)
        # rasterio opened but found no embedded georef: try explicit world file
        if not res.has_georef:
            wf = _world_file_georef(path, res.width, res.height)
            if wf is not None and wf[2] is not None:
                _, crs, bounds = wf
                res.has_georef = True
                res.crs = str(crs) if crs else ""
                res.bounds_wgs84 = bounds
                res.georef_source = "worldfile"
        return res
    except ImportError:
        logger.warning("rasterio not installed — georef metadata limited")
    except Exception as exc:
        logger.warning("rasterio read failed (%s), falling back to Pillow", exc)
    return _read_with_pillow(path)


def write_placeholder_png(dest_path: Path, label: str = "Image", max_side: int = 256) -> None:
    """Fallback thumb when GeoTIFF is too large or rasterio is unavailable."""
    from PIL import ImageDraw

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (max_side, max_side), color=(32, 40, 52))
    draw = ImageDraw.Draw(img)
    lines = [label[:28], "preview N/A"]
    y = max_side // 2 - 20
    for line in lines:
        draw.text((12, y), line, fill=(100, 200, 170))
        y += 18
    img.save(dest_path, format="PNG")


def _rasterio_read_rgb(path: Path, max_side: int):
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling

    with rasterio.open(path) as src:
        count = min(3, src.count)
        scale = min(1.0, max_side / max(src.width, src.height, 1))
        out_h = max(1, int(src.height * scale))
        out_w = max(1, int(src.width * scale))
        data = src.read(
            indexes=list(range(1, count + 1)),
            out_shape=(count, out_h, out_w),
            resampling=Resampling.bilinear,
        )
        if count == 1:
            rgb = np.stack([data[0], data[0], data[0]])
        else:
            rgb = data[:3]
        rgb = np.transpose(rgb, (1, 2, 0)).astype("float32")
        if rgb.max() > 255 or rgb.min() < 0:
            lo, hi = np.percentile(rgb, (2, 98))
            rgb = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) * 255
        return Image.fromarray(rgb.astype("uint8"), mode="RGB")


def load_rgb_pil(path: Path, max_side: Optional[int] = None) -> Image.Image:
    """Load image as RGB PIL, downscaling large GeoTIFFs via rasterio."""
    if max_side is None:
        max_side = get_detection_max_side()
    ext = path.suffix.lower()
    if ext in (".tif", ".tiff"):
        try:
            img = _rasterio_read_rgb(path, max_side)
            return img.copy()
        except ImportError as exc:
            raise RuntimeError(
                "GeoTIFF support requires rasterio. Install with: pip install rasterio"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Could not read GeoTIFF: {exc}") from exc
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return img.copy()


def raster_to_preview_png(src_path: Path, dest_path: Path, max_side: int = 512) -> None:
    """Create RGB thumbnail/preview — uses decimated read for large GeoTIFFs."""
    ext = src_path.suffix.lower()
    if ext in (".tif", ".tiff"):
        try:
            img = _rasterio_read_rgb(src_path, max_side)
            if max(img.size) > max_side:
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest_path, format="PNG")
            return
        except Exception as exc:
            logger.warning("GeoTIFF preview failed for %s: %s", src_path.name, exc)
            write_placeholder_png(dest_path, src_path.name, max_side)
            return

    try:
        with Image.open(src_path) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest_path, format="PNG")
    except Exception as exc:
        logger.warning("Preview failed for %s: %s", src_path.name, exc)
        write_placeholder_png(dest_path, src_path.name, max_side)


@dataclass
class GeorefInfo:
    transform: Any
    crs: Any
    width: int
    height: int
    bounds_wgs84: Optional[Tuple[float, float, float, float]]
    source: str = "embedded"  # embedded | worldfile


def read_georef(path: Path) -> Optional[GeorefInfo]:
    """Read raster affine transform + WGS84 bounds for any raster format.

    Honors embedded CRS (GeoTIFF), GDAL .aux.xml sidecars, and ESRI world files
    for TIFF/PNG/JPEG. Returns None when no georeferencing can be resolved.
    """
    width = height = 0
    try:
        import rasterio
        from rasterio.warp import transform_bounds

        with rasterio.open(path) as src:
            width, height = int(src.width), int(src.height)
            if src.crs is not None:
                bounds = None
                try:
                    w, s, e, n = transform_bounds(src.crs, "EPSG:4326", *src.bounds)
                    bounds = (float(w), float(s), float(e), float(n))
                except Exception as exc:
                    logger.warning("Could not transform bounds for %s: %s", path.name, exc)
                return GeorefInfo(
                    transform=src.transform, crs=src.crs,
                    width=width, height=height, bounds_wgs84=bounds, source="embedded",
                )
    except ImportError:
        return None
    except Exception as exc:
        logger.warning("read_georef rasterio open failed for %s: %s", path.name, exc)

    # No embedded CRS — try an explicit world-file sidecar
    if width <= 0 or height <= 0:
        try:
            with Image.open(path) as img:
                width, height = img.size
        except Exception:
            return None
    wf = _world_file_georef(path, width, height)
    if wf is not None and wf[2] is not None:
        transform, crs, bounds = wf
        return GeorefInfo(
            transform=transform, crs=crs,
            width=width, height=height, bounds_wgs84=bounds, source="worldfile",
        )
    return None


def pixel_to_geo_wgs84(
    x: float,
    y: float,
    georef: GeorefInfo,
    *,
    detection_width: int,
    detection_height: int,
) -> Optional[Tuple[float, float]]:
    """Map detection pixel (x=col, y=row) to WGS84 (lng, lat)."""
    if detection_width <= 0 or detection_height <= 0:
        return None
    try:
        from rasterio.transform import xy as transform_xy
        from rasterio.warp import transform as warp_transform

        scale_x = georef.width / float(detection_width)
        scale_y = georef.height / float(detection_height)
        col = float(x) * scale_x
        row = float(y) * scale_y
        geo_x, geo_y = transform_xy(georef.transform, row, col, offset="center")
        if georef.crs and str(georef.crs) != "EPSG:4326":
            lngs, lats = warp_transform(georef.crs, "EPSG:4326", [geo_x], [geo_y])
            return float(lngs[0]), float(lats[0])
        return float(geo_x), float(geo_y)
    except Exception as exc:
        logger.warning("pixel_to_geo_wgs84 failed: %s", exc)
        return None


def bounds_to_json(bounds: Optional[Tuple[float, float, float, float]]) -> str:
    if not bounds:
        return ""
    return json.dumps({"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]})
