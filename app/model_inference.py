"""
AdaptFormer inference for satellite change detection.

Downloads a pre-trained AdaptFormer model from HuggingFace Hub and runs
tile-based inference on arbitrary-size image pairs, producing a binary
change mask compatible with the rest of the detection pipeline.

Falls back gracefully when torch/transformers are not installed.
"""
import logging
import os

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_MODEL = None
_PROCESSOR = None
_DEVICE = None
_MODEL_ID = "deepang/adaptformer-LEVIR-CD"
_TILE_SIZE = 256  # LEVIR-CD native patch size
_AVAILABLE = None
_LOAD_FAILED = False
_LOAD_ERROR: str | None = None


def _try_import():
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModel
        return torch, AutoImageProcessor, AutoModel
    except ImportError:
        return None, None, None


def _load_model():
    global _MODEL, _PROCESSOR, _DEVICE, _AVAILABLE, _LOAD_FAILED, _LOAD_ERROR
    if _MODEL is not None:
        return _MODEL, _PROCESSOR
    if _LOAD_FAILED:
        raise RuntimeError("AdaptFormer load previously failed")

    torch, AutoImageProcessor, AutoModel = _try_import()
    if torch is None:
        raise RuntimeError("PyTorch/transformers not installed")

    _DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    cache_dir = os.environ.get("HF_HOME", None)
    logger.info("Loading AdaptFormer from %s ...", _MODEL_ID)
    try:
        _PROCESSOR = AutoImageProcessor.from_pretrained(
            _MODEL_ID, cache_dir=cache_dir, trust_remote_code=True)
        _MODEL = AutoModel.from_pretrained(
            _MODEL_ID, cache_dir=cache_dir, trust_remote_code=True)
        _MODEL.to(_DEVICE)
        _MODEL.eval()
        _AVAILABLE = True
        _LOAD_ERROR = None
        logger.info("AdaptFormer loaded on %s", _DEVICE)
    except Exception as exc:
        _LOAD_FAILED = True
        _AVAILABLE = False
        _LOAD_ERROR = str(exc)
        logger.error("AdaptFormer load failed: %s", exc)
        raise
    return _MODEL, _PROCESSOR


def is_model_available():
    """True only if PyTorch is installed and the model loads successfully."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    if _LOAD_FAILED:
        return False
    try:
        _load_model()
        return True
    except Exception:
        return False


def preload_model():
    """Warm-load AdaptFormer at app startup (best-effort)."""
    global _LOAD_ERROR
    try:
        _load_model()
        logger.info("AdaptFormer preload complete")
        return True
    except Exception as exc:
        _LOAD_ERROR = str(exc)
        logger.warning("AdaptFormer preload skipped: %s", exc)
        return False


def get_model_status() -> dict:
    """Status for /health — shows whether AI detection or classical fallback is active."""
    if _AVAILABLE is True:
        mode = "adaptformer_smart_union"
        available = True
    elif _LOAD_FAILED:
        mode = "classical_fallback"
        available = False
    else:
        available = is_model_available()
        mode = "adaptformer_smart_union" if available else "classical_fallback"

    return {
        "modelId": _MODEL_ID,
        "available": available,
        "detectionMode": mode,
        "device": str(_DEVICE) if _DEVICE is not None else None,
        "tta": [op or "identity" for op in _resolve_tta_ops()],
        "error": _LOAD_ERROR,
    }


def _logits_to_change_prob(logits, torch):
    """Robustly convert model logits to a single-channel change probability tile.

    Handles 1-channel (sigmoid), 2+-channel (softmax, change=last channel) and
    already-2D outputs so a change in the upstream model head does not silently
    break inference.
    """
    t = logits
    if t.dim() == 4:  # (N, C, H, W)
        c = t.shape[1]
        if c == 1:
            return torch.sigmoid(t)[0, 0]
        return torch.softmax(t, dim=1)[0, -1]
    if t.dim() == 3:  # (C, H, W) or (N, H, W)
        if t.shape[0] == 1:
            return torch.sigmoid(t)[0]
        # ambiguous: treat as (N,H,W) single map
        return torch.sigmoid(t[0])
    return torch.sigmoid(t)


def _resolve_tta_ops():
    """Choose test-time augmentation flips. Env DETECTION_TTA: off|0 | hflip | full | auto."""
    mode = os.environ.get("DETECTION_TTA", "auto").strip().lower()
    if mode in ("0", "off", "none", "false"):
        return [None]
    if mode in ("h", "hflip"):
        return [None, "h"]
    if mode in ("full", "all"):
        return [None, "h", "v"]
    # auto: lighter on CPU (hflip only), fuller on GPU
    on_cuda = _DEVICE is not None and str(_DEVICE).startswith("cuda")
    return [None, "h", "v"] if on_cuda else [None, "h"]


def _apply_flip(arr, op):
    if op == "h":
        return arr[:, ::-1]
    if op == "v":
        return arr[::-1, :]
    return arr


def _unflip_map(score, op):
    if op == "h":
        return score[:, ::-1]
    if op == "v":
        return score[::-1, :]
    return score


def _infer_score_map(img1, img2, tile_stats=None):
    """Single-pass tiled inference returning a float32 change-probability map at (h, w).

    The model always sees its native 256px patches; the shared tiler handles
    padding, sliding windows and cosine blending so stitching stays consistent
    across every deep model in the project. Tiles that are near-identical
    between img1/img2 skip the model call entirely (see
    ``detection_config.get_skip_unchanged_threshold``); pass ``tile_stats`` to
    accumulate ``{"totalTiles", "skippedTiles"}`` for observability.
    """
    torch, _, _ = _try_import()
    model, processor = _load_model()
    from PIL import Image as PILImage

    from .cd_models.model_utils import tiled_score_map
    from .detection_config import get_tile_batch, get_skip_unchanged_threshold

    def _score_tile(t1, t2):
        pil1 = PILImage.fromarray(t1)
        pil2 = PILImage.fromarray(t2)
        inputs = processor(images=(pil1, pil2), return_tensors="pt")
        inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}
        outputs = model(**inputs)
        logits = outputs.logits
        return _logits_to_change_prob(logits, torch).cpu().numpy()

    def _score_batch(pairs):
        # Stack each pair's processed tensors along the batch dim for one forward
        # pass. Only used when the model's output batch dim matches the number of
        # input pairs; otherwise fall back to correct per-tile scoring.
        n = len(pairs)
        try:
            per_pair = [
                processor(images=(PILImage.fromarray(t1), PILImage.fromarray(t2)),
                          return_tensors="pt")
                for t1, t2 in pairs
            ]
            keys = per_pair[0].keys()
            per_pair_bs = per_pair[0][next(iter(keys))].shape[0]
            stacked = {k: torch.cat([p[k] for p in per_pair], dim=0).to(_DEVICE)
                       for k in keys}
            outputs = model(**stacked)
            logits = outputs.logits
            if logits.shape[0] != n * per_pair_bs:
                raise RuntimeError("unexpected batched logits layout")
            group = per_pair_bs
            return [
                _logits_to_change_prob(logits[i * group:(i + 1) * group], torch).cpu().numpy()
                for i in range(n)
            ]
        except Exception as exc:
            logger.warning("Batched tile inference failed (%s); using per-tile", exc)
            return [_score_tile(t1, t2) for t1, t2 in pairs]

    batch = get_tile_batch()
    with torch.no_grad():
        return tiled_score_map(_score_tile, img1, img2,
                               tile_size=_TILE_SIZE, overlap=_TILE_SIZE // 4,
                               score_batch_fn=_score_batch if batch > 1 else None,
                               batch=batch,
                               skip_unchanged_threshold=get_skip_unchanged_threshold(),
                               stats=tile_stats)


def _count_windows(full_h, full_w, tile_size, overlap):
    step = max(1, int(round(tile_size * (1.0 - overlap))))
    ny = len(range(0, max(1, full_h - tile_size + 1), step)) + (1 if full_h > tile_size else 0)
    nx = len(range(0, max(1, full_w - tile_size + 1), step)) + (1 if full_w > tile_size else 0)
    return max(1, ny) * max(1, nx)


def predict_change_score_windowed(path_a, path_b, out_h, out_w,
                                  tile_size=512, overlap=0.25, on_progress=None):
    """Build a change-probability map from two large GeoTIFFs via disk windows.

    Reads paired native-resolution windows (so the model sees full detail),
    scores each with AdaptFormer, then accumulates into a bounded
    ``(out_h, out_w)`` canvas with cosine blending. Peak memory stays at one
    native window plus the small output canvas, so 10k+ rasters never OOM.
    ``on_progress(frac)`` (0..1) is called as windows complete.
    Returns a float32 score map in [0, 1] at (out_h, out_w).
    """
    from .dda.geotiff_io import iter_geotiff_window_pairs, read_native_size

    _load_model()

    score_sum = np.zeros((out_h, out_w), dtype=np.float32)
    count = np.zeros((out_h, out_w), dtype=np.float32)
    scale_y = scale_x = None

    total = None
    native = read_native_size(__import__("pathlib").Path(path_a))
    if native:
        total = _count_windows(native[1], native[0], tile_size, overlap)
    done = 0

    for tile_a, tile_b, y0, x0, full_h, full_w in iter_geotiff_window_pairs(
            path_a, path_b, tile_size=tile_size, overlap=overlap):
        if scale_y is None:
            scale_y = out_h / float(full_h)
            scale_x = out_w / float(full_w)
            if total is None:
                total = _count_windows(full_h, full_w, tile_size, overlap)

        wh, ww = tile_a.shape[:2]
        score = _infer_score_map(tile_a, tile_b)

        dy0 = int(round(y0 * scale_y))
        dx0 = int(round(x0 * scale_x))
        dh = max(1, int(round(wh * scale_y)))
        dw = max(1, int(round(ww * scale_x)))
        dy1 = min(out_h, dy0 + dh)
        dx1 = min(out_w, dx0 + dw)
        dh, dw = dy1 - dy0, dx1 - dx0
        if dh <= 0 or dw <= 0:
            continue

        score_ds = cv2.resize(score, (dw, dh), interpolation=cv2.INTER_AREA)
        wy = np.hanning(dh + 2)[1:-1] if dh > 2 else np.ones(dh)
        wx = np.hanning(dw + 2)[1:-1] if dw > 2 else np.ones(dw)
        weight = np.maximum(np.outer(wy, wx).astype(np.float32), 1e-3)

        score_sum[dy0:dy1, dx0:dx1] += score_ds * weight
        count[dy0:dy1, dx0:dx1] += weight

        done += 1
        if on_progress and total:
            try:
                on_progress(min(1.0, done / float(total)))
            except Exception:
                pass

    count = np.maximum(count, 1e-6)
    return score_sum / count


def _predict_score_tta(img1, img2, tile_stats=None):
    """TTA-averaged change score for one (already same-size) image pair."""
    h, w = img1.shape[:2]
    ops = _resolve_tta_ops()
    acc = np.zeros((h, w), dtype=np.float32)
    n = 0
    for op in ops:
        a1 = np.ascontiguousarray(_apply_flip(img1, op))
        a2 = np.ascontiguousarray(_apply_flip(img2, op))
        try:
            s = _infer_score_map(a1, a2, tile_stats=tile_stats)
        except Exception as exc:
            logger.warning("TTA pass %s failed: %s", op, exc)
            continue
        acc += _unflip_map(s, op)
        n += 1
    if n == 0:
        raise RuntimeError("AdaptFormer inference produced no predictions")
    return acc / float(n)


def predict_change_mask(img1, img2, threshold=0.5, tile_stats=None):
    """
    Run AdaptFormer inference on two RGB numpy arrays (H, W, 3).
    Averages predictions over test-time augmentation flips (DETECTION_TTA) and,
    when DETECTION_MULTISCALE is set, fuses scores across scales (max) so both
    small and large changes are captured.
    Returns (uint8 mask [0 or 255], float32 score map [0-1]).
    Use threshold > 1.0 to obtain score map only (empty mask).
    Pass ``tile_stats`` (a dict) to accumulate tile pre-filter counters
    (``totalTiles``/``skippedTiles``) across every TTA/scale pass.
    """
    _load_model()

    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h, w = img1.shape[:2]

    from .detection_config import get_multiscale_scales
    scales = get_multiscale_scales()

    if not scales:
        avg_score = _predict_score_tta(img1, img2, tile_stats=tile_stats)
    else:
        fused = None
        for scale in scales:
            if scale == 1.0:
                s1, s2 = img1, img2
            else:
                nh = max(64, int(round(h * scale)))
                nw = max(64, int(round(w * scale)))
                interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                s1 = cv2.resize(img1, (nw, nh), interpolation=interp)
                s2 = cv2.resize(img2, (nw, nh), interpolation=interp)
            score = _predict_score_tta(s1, s2, tile_stats=tile_stats)
            if score.shape != (h, w):
                score = cv2.resize(score, (w, h), interpolation=cv2.INTER_LINEAR)
            # Max fusion favors recall on small structures that only one scale sees
            fused = score if fused is None else np.maximum(fused, score)
        avg_score = fused

    mask = (avg_score >= threshold).astype(np.uint8) * 255
    return mask, avg_score
