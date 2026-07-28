"""Before/after pair pre-alignment and input-quality guard.

This module sits *in front of* the detection engine and answers one question
that the engine currently assumes rather than checks: **are these two images
actually a comparable, co-registered pair of the same ground?**

Three real-world failure modes motivated it (all observed on drone GeoTIFFs):

1. **Identical inputs** — the same file uploaded twice. The engine dutifully
   reports "no change"; the operator reads that as a detection failure. We
   catch it up front with a cheap content hash.
2. **Different pixel grids** — two georeferenced rasters of the same place at
   different GSD / extent / band count. Naive ``cv2.resize`` to a common shape
   *stretches* rather than *aligns*, so static ground shows up as change. We
   reproject BOTH onto one common grid (their geographic overlap, at the finer
   resolution) so a pixel means the same ground in both.
3. **Un-registerable frames** — raw (non-orthorectified) frames shot from
   different viewpoints. No 2D transform aligns them (parallax). We can't fix
   that here, but we *measure* it and emit an honest warning instead of a
   silent garbage mask.

The engine's detection path is untouched; callers opt in. A CLI is provided so
the alignment of any pair can be checked without running a full job:

    python -m app.dda.pair_align before.tif after.tif [--out DIR]
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class PairAlignResult:
    """Outcome of aligning + assessing a before/after pair.

    ``status`` is the single field a caller should branch on:
      * ``"identical"``   — inputs are the same image; there is nothing to detect.
      * ``"ok"``          — aligned and alignment quality is adequate.
      * ``"low_quality"`` — aligned but residual misalignment is high; detection
                            will be unreliable (likely raw/un-orthorectified
                            frames). Results should be shown with a warning.
      * ``"error"``       — alignment could not be attempted (see ``message``).
    """
    status: str
    message: str
    ncc: float = 0.0
    method: str = "none"
    grid: Optional[Tuple[int, int]] = None  # (width, height) of aligned output
    overlap_frac: float = 0.0

    def to_json(self) -> dict:
        d = asdict(self)
        if self.grid is not None:
            d["grid"] = list(self.grid)
        return d


# ---------------------------------------------------------------------------
# 1. Identical-input guard
# ---------------------------------------------------------------------------

def content_hash(arr: np.ndarray) -> str:
    """Stable MD5 of raw pixel bytes — cheap identical-pair detector."""
    return hashlib.md5(np.ascontiguousarray(arr).tobytes()).hexdigest()


def are_identical(before: np.ndarray, after: np.ndarray) -> bool:
    """True when the two arrays are pixel-for-pixel identical."""
    if before.shape != after.shape:
        return False
    return content_hash(before) == content_hash(after)


# ---------------------------------------------------------------------------
# 2. Geographic grid alignment (the correct alignment for georeferenced pairs)
# ---------------------------------------------------------------------------

def geo_align_pair(before_path: Path, after_path: Path
                   ) -> Optional[Tuple[np.ndarray, np.ndarray, float]]:
    """Reproject both rasters onto one common grid over their geographic overlap.

    Returns ``(before_rgb, after_rgb, overlap_frac)`` as HxWx3 uint8 arrays on
    an identical grid, or ``None`` when either input is not georeferenced or the
    footprints do not overlap. ``overlap_frac`` is the overlap area as a
    fraction of the *after* footprint — a low value means the two rasters barely
    cover the same ground.
    """
    try:
        import rasterio
        from rasterio.transform import from_origin
        from rasterio.warp import Resampling, reproject
    except ImportError:
        logger.warning("rasterio unavailable — cannot geo-align pair")
        return None

    try:
        b = rasterio.open(str(before_path))
        a = rasterio.open(str(after_path))
    except Exception as exc:
        logger.warning("geo_align_pair: could not open rasters: %s", exc)
        return None

    with b, a:
        if b.crs is None or a.crs is None:
            return None
        # Work in the after image's CRS; transform the before bounds into it.
        try:
            from rasterio.warp import transform_bounds
            b_in_a = transform_bounds(b.crs, a.crs, *b.bounds)
        except Exception:
            b_in_a = b.bounds

        left = max(b_in_a[0], a.bounds.left)
        bottom = max(b_in_a[1], a.bounds.bottom)
        right = min(b_in_a[2], a.bounds.right)
        top = min(b_in_a[3], a.bounds.top)
        if right <= left or top <= bottom:
            return None  # no geographic overlap

        a_area = (a.bounds.right - a.bounds.left) * (a.bounds.top - a.bounds.bottom)
        overlap_frac = ((right - left) * (top - bottom)) / max(a_area, 1e-9)

        res_x, res_y = a.res
        width = max(1, int(round((right - left) / res_x)))
        height = max(1, int(round((top - bottom) / res_y)))
        dst_transform = from_origin(left, top, res_x, res_y)

        def _warp(src) -> np.ndarray:
            out = np.zeros((3, height, width), dtype=np.uint8)
            for i in range(3):  # first three bands = RGB; ignore any alpha
                reproject(
                    source=rasterio.band(src, i + 1),
                    destination=out[i],
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=dst_transform, dst_crs=a.crs,
                    resampling=Resampling.bilinear,
                )
            return np.ascontiguousarray(out.transpose(1, 2, 0))

        return _warp(b), _warp(a), float(overlap_frac)


# ---------------------------------------------------------------------------
# 3. Alignment-quality assessment
# ---------------------------------------------------------------------------

def _ncc(gray1: np.ndarray, gray2: np.ndarray) -> float:
    """Normalized cross-correlation of two same-size grayscale images."""
    a = gray1.astype(np.float32).ravel()
    b = gray2.astype(np.float32).ravel()
    if a.size != b.size or a.size < 64:
        return 0.0
    c = np.corrcoef(a, b)[0, 1]
    return float(c) if np.isfinite(c) else 0.0


def assess_alignment(before: np.ndarray, after: np.ndarray,
                     ncc_ok: float = 0.45) -> Tuple[str, float]:
    """Judge whether an already-same-grid pair is well enough aligned to detect.

    Uses global NCC on the shared region. A well-registered VHR pair — even with
    genuine change present — keeps most of the static scene correlated, so NCC
    stays high; two frames off by rotation/parallax collapse toward zero. Returns
    ``(status, ncc)`` where status is ``"ok"`` or ``"low_quality"``.
    """
    if before.shape != after.shape:
        after = cv2.resize(after, (before.shape[1], before.shape[0]))
    g1 = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY)
    ncc = _ncc(g1, g2)
    return ("ok" if ncc >= ncc_ok else "low_quality"), ncc


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------

def prepare_pair(before_path: Path, after_path: Path
                 ) -> Tuple[Optional[np.ndarray], Optional[np.ndarray], PairAlignResult]:
    """Load, guard, and align a pair for detection.

    Returns ``(before_rgb, after_rgb, result)``. When ``result.status`` is
    ``"identical"`` or ``"error"`` the arrays may be ``None`` — callers should
    surface ``result.message`` to the operator instead of running detection.
    On ``"ok"``/``"low_quality"`` the arrays are on a common grid and ready for
    the engine (detection still runs on ``"low_quality"``, just with a warning).
    """
    before_path, after_path = Path(before_path), Path(after_path)

    aligned = geo_align_pair(before_path, after_path)
    if aligned is not None:
        before, after, overlap = aligned
        method = "geo_reproject"
    else:
        # No georeferencing: fall back to a plain load + resize-to-match so the
        # engine's own SIFT/ORB/ECC registration can still take over downstream.
        from .geotiff_io import load_rgb_pil
        before = np.array(load_rgb_pil(before_path))[:, :, :3]
        after = np.array(load_rgb_pil(after_path))[:, :, :3]
        if before.shape != after.shape:
            before = cv2.resize(before, (after.shape[1], after.shape[0]))
        overlap = 1.0
        method = "resize_only"

    if are_identical(before, after):
        return None, None, PairAlignResult(
            status="identical",
            message=("Before and after are the same image (identical pixels). "
                     "There is no change to detect - check that two different "
                     "dates were selected."),
            method=method, grid=(before.shape[1], before.shape[0]),
            overlap_frac=overlap,
        )

    status, ncc = assess_alignment(before, after)
    grid = (before.shape[1], before.shape[0])
    if status == "ok":
        msg = f"Pair aligned via {method} (NCC={ncc:.2f}, overlap={overlap:.0%})."
    else:
        msg = (f"Pair is poorly aligned (NCC={ncc:.2f}). The two images do not "
               "register - likely raw drone frames from different viewpoints "
               "rather than orthomosaics on a common grid. Detection will be "
               "unreliable; export both dates as north-up orthomosaics on the "
               "same grid for accurate results.")
    return before, after, PairAlignResult(
        status=status, message=msg, ncc=round(ncc, 4),
        method=method, grid=grid, overlap_frac=round(overlap, 4),
    )


def _main(argv=None) -> int:
    import argparse

    from PIL import Image

    ap = argparse.ArgumentParser(description="Check before/after pair alignment.")
    ap.add_argument("before")
    ap.add_argument("after")
    ap.add_argument("--out", help="dir to write aligned before/after PNGs")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    before, after, res = prepare_pair(Path(args.before), Path(args.after))
    print(f"status      : {res.status}")
    print(f"message     : {res.message}")
    print(f"method      : {res.method}")
    print(f"ncc         : {res.ncc}")
    print(f"overlap     : {res.overlap_frac}")
    print(f"grid (w x h): {res.grid}")
    if args.out and before is not None:
        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        Image.fromarray(before).save(out / "aligned_before.png")
        Image.fromarray(after).save(out / "aligned_after.png")
        print(f"wrote aligned PNGs to {out}")
    return 0 if res.status in ("ok", "low_quality") else 1


if __name__ == "__main__":
    raise SystemExit(_main())
