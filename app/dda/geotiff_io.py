"""GeoTIFF ingest and preview generation (FR-02). Rasterio optional at import time."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

from .config import get_detection_max_side

logger = logging.getLogger(__name__)


@dataclass
class IngestResult:
    width: int
    height: int
    has_georef: bool
    crs: str
    bounds_wgs84: Optional[Tuple[float, float, float, float]]  # west, south, east, north
    format: str


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
        return IngestResult(
            width=int(src.width),
            height=int(src.height),
            has_georef=has_georef,
            crs=crs,
            bounds_wgs84=bounds,
            format="geotiff",
        )


def _read_with_pillow(path: Path) -> IngestResult:
    with Image.open(path) as img:
        w, h = img.size
    ext = path.suffix.lower()
    fmt = "geotiff" if ext in (".tif", ".tiff") else "image"
    return IngestResult(
        width=w,
        height=h,
        has_georef=False,
        crs="",
        bounds_wgs84=None,
        format=fmt,
    )


def inspect_image(path: Path) -> IngestResult:
    ext = path.suffix.lower()
    if ext in (".tif", ".tiff"):
        try:
            return _read_with_rasterio(path)
        except ImportError:
            logger.warning("rasterio not installed — GeoTIFF metadata limited")
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
            img = img.convert("RGB")
            img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(dest_path, format="PNG")
    except Exception as exc:
        logger.warning("Preview failed for %s: %s", src_path.name, exc)
        write_placeholder_png(dest_path, src_path.name, max_side)


def bounds_to_json(bounds: Optional[Tuple[float, float, float, float]]) -> str:
    if not bounds:
        return ""
    return json.dumps({"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]})
