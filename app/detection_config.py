"""Centralized, env-driven detection configuration.

Single source of truth for detection tuning knobs so the engine, model
inference, and tiling utilities stay consistent. Every value is read from the
environment with conservative defaults that preserve the previous behavior
(``downscaled`` inference, ``smart_union`` fusion, no multi-scale).

Env vars
--------
DETECTION_MAX_SIDE        Max pixel dimension for the legacy downscaled path.
DETECTION_INFERENCE_MODE  ``downscaled`` (default) | ``fullres_tiled``.
DETECTION_FULLRES_MAX_SIDE Cap for full-resolution tiled mode (0 = native).
DETECTION_TILE_SIZE       Tile size for full-res scoring (256..2048).
DETECTION_TILE_OVERLAP    Fractional tile overlap (0.0..0.5).
DETECTION_MULTISCALE      ``off`` | comma list e.g. ``0.5,1.0,1.5``.
DETECTION_FUSION          ``smart_union`` (default) | ``hysteresis``.
DETECTION_SKIP_PREBLUR    ``true`` | ``false`` (auto: on for fullres_tiled).
DETECTION_TTA             ``off`` | ``hflip`` | ``full`` | ``auto`` (model_inference).
DETECTION_SKIP_UNCHANGED_THRESHOLD  Worst-cell LAB tile-signature distance below
                          which a tile skips the deep model (0/unset = off).
"""
from __future__ import annotations

import logging
import os
from typing import List

_log = logging.getLogger(__name__)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def get_detection_max_side() -> int:
    """Max pixel dimension for image load + the downscaled detection path."""
    hosted = bool(_env("SPACE_ID"))
    default = "2048" if hosted else "4096"
    try:
        value = int(os.environ.get("DETECTION_MAX_SIDE", default))
    except ValueError:
        value = int(default)
    return max(1024, min(8192, value))


def get_inference_mode() -> str:
    """Detection inference mode: ``downscaled`` or ``fullres_tiled``."""
    mode = _env("DETECTION_INFERENCE_MODE", "downscaled").lower()
    return mode if mode in ("downscaled", "fullres_tiled") else "downscaled"


def get_fullres_max_side() -> int:
    """Upper bound for full-res tiled mode. 0 means use native resolution.

    Defaults to 8192 so very large GeoTIFFs stay bounded in RAM while still
    running detection at far higher detail than the 2048/4096 downscaled cap.
    """
    raw = _env("DETECTION_FULLRES_MAX_SIDE", "8192")
    try:
        value = int(raw)
    except ValueError:
        value = 8192
    if value <= 0:
        return 0  # native resolution, no cap
    return max(2048, min(20000, value))


def get_load_max_side() -> int:
    """Effective image-load / inference pixel cap for the active mode.

    In ``fullres_tiled`` mode this returns the (much larger) full-res cap so
    images keep their detail; otherwise it returns the standard downscale cap.
    Always a concrete positive int so callers can pass it straight to loaders.
    """
    if get_inference_mode() == "fullres_tiled":
        cap = get_fullres_max_side()
        return cap if cap > 0 else 20000
    return get_detection_max_side()


def get_windowed_threshold() -> int:
    """Native max-side above which GeoTIFF inference streams via rasterio windows.

    Keeps peak RAM bounded for very large rasters: when a GeoTIFF's native
    longest side exceeds this, the deep score map is built tile-by-tile from
    disk instead of loading the whole array. 0 disables windowed streaming.
    """
    raw = _env("DETECTION_WINDOWED_THRESHOLD", "8192")
    try:
        value = int(raw)
    except ValueError:
        value = 8192
    return max(0, value)


def get_tile_size() -> int:
    """Tile size (px) for full-resolution scoring."""
    try:
        value = int(os.environ.get("DETECTION_TILE_SIZE", "512"))
    except ValueError:
        value = 512
    return max(256, min(2048, value))


def get_tile_overlap() -> float:
    """Fractional overlap between adjacent tiles (0.0..0.5)."""
    try:
        value = float(os.environ.get("DETECTION_TILE_OVERLAP", "0.25"))
    except ValueError:
        value = 0.25
    return min(0.5, max(0.0, value))


def get_multiscale_scales() -> List[float]:
    """Scales for multi-scale fusion. Empty list disables it."""
    raw = _env("DETECTION_MULTISCALE", "off").lower()
    if raw in ("off", "", "none", "0", "false"):
        return []
    scales: List[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            scale = float(part)
        except ValueError:
            continue
        if 0.1 <= scale <= 4.0:
            scales.append(scale)
    # Always include native scale so recall never drops below single-scale
    if scales and 1.0 not in scales:
        scales.append(1.0)
    return sorted(set(scales))


def get_fusion_mode() -> str:
    """DL + classical fusion strategy: ``smart_union`` or ``hysteresis``."""
    mode = _env("DETECTION_FUSION", "smart_union").lower()
    return mode if mode in ("smart_union", "hysteresis") else "smart_union"


def get_skip_preblur() -> bool:
    """Whether to skip the preprocessing Gaussian/bilateral blur.

    Explicit env wins; otherwise auto-skip in full-res mode to preserve the
    fine detail that motivates running detection at native resolution.
    """
    raw = _env("DETECTION_SKIP_PREBLUR", "").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return get_inference_mode() == "fullres_tiled"


def _flag(name: str, default: bool) -> bool:
    raw = _env(name, "").lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return default


def get_enable_clahe() -> bool:
    """CLAHE contrast equalization during radiometric normalization (default on)."""
    return _flag("DETECTION_CLAHE", True)


def get_hist_match() -> bool:
    """Optional histogram matching of the after image to the before (default off)."""
    return _flag("DETECTION_HIST_MATCH", False)


def get_tile_batch() -> int:
    """Tiles per model forward pass (GPU throughput). 1 = no batching (default)."""
    try:
        value = int(os.environ.get("DETECTION_TILE_BATCH", "1"))
    except ValueError:
        value = 1
    return max(1, min(64, value))


def get_tile_memory_budget_mb() -> int:
    """Soft RAM budget (MB) for a single in-memory detection array.

    When loading both timestamps at the full-res cap would exceed this, large
    GeoTIFFs switch to disk-windowed streaming so peak memory stays bounded
    (lets 15 GB rasters run without OOM). 0 disables the budget check.
    """
    raw = _env("DETECTION_TILE_MEMORY_MB", "1536")
    try:
        value = int(raw)
    except ValueError:
        value = 1536
    return max(0, value)


def get_skip_unchanged_threshold() -> float | None:
    """Worst-cell (max) LAB-signature distance below which a tile is unchanged.

    When set (> 0), tiles whose before/after content is near-identical skip the
    expensive deep-model forward pass entirely during tiled inference — most
    before/after satellite tiles didn't change, so this cuts CPU inference time
    roughly in proportion to how static the scene is, without touching quality
    on tiles that actually changed. 0/unset disables it (previous behavior).
    """
    raw = _env("DETECTION_SKIP_UNCHANGED_THRESHOLD", "0")
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    return value if value > 0 else None


def get_save_prob_map() -> bool:
    """Save a downsampled probability-map PNG per run for debugging (default off)."""
    return _flag("DETECTION_SAVE_PROB_MAP", False)


def get_border_margin() -> int:
    """Border pixels zeroed in mask cleanup. Smaller in full-res mode."""
    raw = _env("DETECTION_BORDER_MARGIN", "")
    if raw:
        try:
            return max(0, min(64, int(raw)))
        except ValueError:
            pass
    return 4 if get_inference_mode() == "fullres_tiled" else 12


def summary() -> dict:
    """Snapshot of effective detection config (for logs and debug output)."""
    return {
        "maxSide": get_detection_max_side(),
        "inferenceMode": get_inference_mode(),
        "fullresMaxSide": get_fullres_max_side(),
        "tileSize": get_tile_size(),
        "tileOverlap": get_tile_overlap(),
        "multiscale": get_multiscale_scales(),
        "fusion": get_fusion_mode(),
        "skipPreblur": get_skip_preblur(),
        "borderMargin": get_border_margin(),
        "skipUnchangedThreshold": get_skip_unchanged_threshold(),
    }
