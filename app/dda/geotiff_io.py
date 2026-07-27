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
    Corrupt / truncated GeoTIFFs must not crash library rescan — callers get a
    zero-size stub and should skip indexing.
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
    try:
        return _read_with_pillow(path)
    except Exception as exc:
        # Common for truncated / BigTIFF files Pillow cannot parse
        # ("Invalid dimensions"). Don't abort the whole library sync.
        logger.warning("Pillow inspect failed for %s: %s", path.name, exc)
        ext = path.suffix.lower()
        return IngestResult(
            width=0,
            height=0,
            has_georef=False,
            crs="",
            bounds_wgs84=None,
            format="geotiff" if ext in (".tif", ".tiff") else "image",
            georef_source="none",
        )


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

        read_kwargs = {
            "indexes": list(range(1, count + 1)),
            "out_shape": (count, out_h, out_w),
            "resampling": Resampling.bilinear,
        }
        try:
            data = src.read(**read_kwargs)
        except Exception:
            # Fallback: read from coarsest overview when full decimation fails
            if src.overviews(1):
                with rasterio.open(path, overview_level=len(src.overviews(1))) as ov_src:
                    ocount = min(3, ov_src.count)
                    data = ov_src.read(
                        indexes=list(range(1, ocount + 1)),
                        out_shape=(ocount, out_h, out_w),
                        resampling=Resampling.bilinear,
                    )
            else:
                raise

        if count == 1:
            rgb = np.stack([data[0], data[0], data[0]])
        else:
            rgb = data[:3]
        rgb = np.transpose(rgb, (1, 2, 0)).astype("float32")
        if rgb.max() > 255 or rgb.min() < 0:
            lo, hi = np.percentile(rgb, (2, 98))
            rgb = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) * 255
        return Image.fromarray(rgb.astype("uint8"), mode="RGB")


def read_native_size(path: Path) -> Optional[Tuple[int, int]]:
    """Return (width, height) of a GeoTIFF without decoding pixels, or None."""
    ext = path.suffix.lower()
    if ext not in (".tif", ".tiff"):
        return None
    try:
        import rasterio
        with rasterio.open(path) as src:
            return int(src.width), int(src.height)
    except Exception:
        return None


def _normalize_window_rgb(data):
    """Convert a rasterio (bands, h, w) window read to RGB uint8 (h, w, 3)."""
    import numpy as np
    count = data.shape[0]
    if count == 1:
        rgb = np.stack([data[0], data[0], data[0]])
    else:
        rgb = data[:3]
    rgb = np.transpose(rgb, (1, 2, 0)).astype("float32")
    if rgb.max() > 255 or rgb.min() < 0:
        lo, hi = np.percentile(rgb, (2, 98))
        rgb = np.clip((rgb - lo) / max(hi - lo, 1e-6), 0, 1) * 255
    return rgb.astype("uint8")


def iter_geotiff_window_pairs(path_a: Path, path_b: Path,
                              tile_size: int = 512, overlap: float = 0.25):
    """Stream paired native-resolution RGB windows from two GeoTIFFs.

    Reads windows from disk via rasterio so very large rasters never load
    fully into RAM. Yields ``(tile_a, tile_b, y0, x0, full_h, full_w)`` where
    tiles are RGB uint8 on **path_a's** pixel grid.

    Same-size pairs use direct window reads. Different pixel sizes (common for
    same-bounds DDA pairs with slight GSD drift) reproject ``path_b`` into each
    ``path_a`` window via georef so windowed full-res inference still works.
    """
    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.windows import Window, transform as window_transform

    tile_size = max(64, int(tile_size))
    ov = min(0.5, max(0.0, float(overlap)))
    step = max(1, int(round(tile_size * (1.0 - ov))))

    with rasterio.open(path_a) as src_a, rasterio.open(path_b) as src_b:
        full_w, full_h = int(src_a.width), int(src_a.height)
        same_grid = (src_a.width, src_a.height) == (src_b.width, src_b.height)
        bands_a = list(range(1, min(3, src_a.count) + 1))
        bands_b = list(range(1, min(3, src_b.count) + 1))
        n_bands = len(bands_a)

        ys = list(range(0, max(1, full_h - tile_size + 1), step))
        xs = list(range(0, max(1, full_w - tile_size + 1), step))
        if ys[-1] != full_h - tile_size and full_h > tile_size:
            ys.append(full_h - tile_size)
        if xs[-1] != full_w - tile_size and full_w > tile_size:
            xs.append(full_w - tile_size)

        for y0 in ys:
            for x0 in xs:
                wh = min(tile_size, full_h - y0)
                ww = min(tile_size, full_w - x0)
                win = Window(x0, y0, ww, wh)
                da = src_a.read(indexes=bands_a, window=win)

                if same_grid:
                    db = src_b.read(indexes=bands_b, window=win)
                else:
                    # Warp after → before window CRS/transform/size
                    dst = np.zeros((n_bands, wh, ww), dtype=np.float32)
                    dst_transform = window_transform(win, src_a.transform)
                    for i, band in enumerate(bands_b[:n_bands]):
                        reproject(
                            source=rasterio.band(src_b, band),
                            destination=dst[i],
                            src_transform=src_b.transform,
                            src_crs=src_b.crs,
                            dst_transform=dst_transform,
                            dst_crs=src_a.crs,
                            resampling=Resampling.bilinear,
                        )
                    db = dst

                yield (_normalize_window_rgb(da), _normalize_window_rgb(db),
                       y0, x0, full_h, full_w)


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


def read_gsd_meters(path: Path) -> Optional[float]:
    """Ground sample distance (meters/pixel) from the raster's georeferencing.

    For geographic CRS (degrees) the pixel size is converted to meters at the
    raster's center latitude. Returns None when no usable georef exists.
    """
    info = read_georef(path)
    if info is None or info.transform is None:
        return None
    try:
        px_x = abs(float(info.transform.a))
        px_y = abs(float(info.transform.e))
        if px_x <= 0 or px_y <= 0:
            return None
        gsd = (px_x + px_y) / 2.0
        crs = info.crs
        is_geographic = False
        try:
            is_geographic = bool(crs is not None and crs.is_geographic)
        except Exception:
            is_geographic = bool(crs is not None and "4326" in str(crs))
        if is_geographic:
            import math
            lat = 0.0
            if info.bounds_wgs84:
                lat = (info.bounds_wgs84[1] + info.bounds_wgs84[3]) / 2.0
            meters_per_deg = 111320.0 * max(0.2, math.cos(math.radians(lat)))
            gsd *= meters_per_deg
        return float(gsd) if gsd > 0 else None
    except Exception as exc:
        logger.warning("read_gsd_meters failed for %s: %s", path.name, exc)
        return None


def harmonize_pair_gsd(
    before_pil: Image.Image,
    after_pil: Image.Image,
    before_path: Path,
    after_path: Path,
):
    """Resample a loaded image pair to a common (coarser) ground resolution.

    SRCDNet motivation: when the two acquisitions have different GSDs, naive
    pixel-grid alignment upsamples the coarser image and the interpolated
    texture produces spurious "changes". Downsampling BOTH to the coarser GSD
    (area-weighted) avoids hallucinated texture diffs.

    Accounts for any load-time downscaling by scaling each file's native GSD
    with its loaded/native width ratio. Returns ``(before, after, debug)``
    where debug is None when harmonization was skipped or not needed.
    """
    from ..detection_config import get_gsd_harmonize, get_gsd_tolerance

    if not get_gsd_harmonize():
        return before_pil, after_pil, None
    try:
        gsd_b = read_gsd_meters(before_path)
        gsd_a = read_gsd_meters(after_path)
        if not gsd_b or not gsd_a:
            return before_pil, after_pil, None

        native_b = read_native_size(before_path)
        native_a = read_native_size(after_path)
        if not native_b or not native_a:
            return before_pil, after_pil, None

        # Effective GSD of the in-memory image after load-time downscaling
        eff_b = gsd_b * (native_b[0] / float(before_pil.size[0]))
        eff_a = gsd_a * (native_a[0] / float(after_pil.size[0]))

        rel_diff = abs(eff_b - eff_a) / max(eff_b, eff_a)
        tolerance = get_gsd_tolerance()
        debug = {
            "gsdBefore": round(eff_b, 4),
            "gsdAfter": round(eff_a, 4),
            "relDiff": round(rel_diff, 4),
            "tolerance": tolerance,
            "harmonized": False,
        }
        if rel_diff <= tolerance:
            return before_pil, after_pil, debug

        target = max(eff_b, eff_a)  # coarser resolution wins
        out = []
        for img, eff in ((before_pil, eff_b), (after_pil, eff_a)):
            scale = eff / target
            if scale < 0.999:
                new_size = (max(64, round(img.size[0] * scale)),
                            max(64, round(img.size[1] * scale)))
                # BOX = area-weighted downsample (no hallucinated texture)
                img = img.resize(new_size, Image.Resampling.BOX)
            out.append(img)
        debug["harmonized"] = True
        debug["targetGsd"] = round(target, 4)
        debug["sizeBefore"] = list(out[0].size)
        debug["sizeAfter"] = list(out[1].size)
        logger.info("GSD harmonized to %.3f m/px (before %.3f, after %.3f)",
                    target, eff_b, eff_a)
        return out[0], out[1], debug
    except Exception as exc:
        logger.warning("GSD harmonization failed: %s", exc)
        return before_pil, after_pil, None


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
