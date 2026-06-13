"""GeoTIFF ingest and preview generation (FR-02). Rasterio optional at import time."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image

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


def raster_to_preview_png(src_path: Path, dest_path: Path, max_side: int = 512) -> None:
    """Create RGB thumbnail/preview from GeoTIFF or raster image."""
    ext = src_path.suffix.lower()
    if ext in (".tif", ".tiff"):
        try:
            import numpy as np
            import rasterio

            with rasterio.open(src_path) as src:
                count = min(3, src.count)
                data = src.read(indexes=list(range(1, count + 1)))
                if count == 1:
                    rgb = np.stack([data[0], data[0], data[0]])
                else:
                    rgb = data[:3]
                rgb = np.transpose(rgb, (1, 2, 0)).astype("float32")
                if rgb.max() > 255 or rgb.min() < 0:
                    lo, hi = np.percentile(rgb, (2, 98))
                    rgb = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) * 255
                img = Image.fromarray(rgb.astype("uint8"), mode="RGB")
                img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                img.save(dest_path, format="PNG")
                return
        except Exception as exc:
            logger.warning("GeoTIFF preview via rasterio failed: %s", exc)

    with Image.open(src_path) as img:
        img = img.convert("RGB")
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(dest_path, format="PNG")


def bounds_to_json(bounds: Optional[Tuple[float, float, float, float]]) -> str:
    if not bounds:
        return ""
    return json.dumps({"west": bounds[0], "south": bounds[1], "east": bounds[2], "north": bounds[3]})
