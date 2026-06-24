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


def _infer_score_map(img1, img2):
    """Single-pass tiled inference returning a float32 change-probability map at (h, w)."""
    torch, _, _ = _try_import()
    model, processor = _load_model()
    from PIL import Image as PILImage

    h, w = img1.shape[:2]
    tile = _TILE_SIZE
    overlap = tile // 4
    stride = tile - overlap

    pad_h = (tile - h % tile) % tile
    pad_w = (tile - w % tile) % tile
    if pad_h or pad_w:
        img1 = np.pad(img1, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
        img2 = np.pad(img2, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    ph, pw = img1.shape[:2]
    score_sum = np.zeros((ph, pw), dtype=np.float32)
    count = np.zeros((ph, pw), dtype=np.float32)

    ramp = np.linspace(0, 1, overlap)
    flat = np.ones(tile - 2 * overlap)
    profile = np.concatenate([ramp, flat, ramp[::-1]])
    weight_2d = np.outer(profile, profile).astype(np.float32)

    with torch.no_grad():
        for y0 in range(0, ph - tile + 1, stride):
            for x0 in range(0, pw - tile + 1, stride):
                t1 = np.ascontiguousarray(img1[y0:y0+tile, x0:x0+tile])
                t2 = np.ascontiguousarray(img2[y0:y0+tile, x0:x0+tile])

                pil1 = PILImage.fromarray(t1)
                pil2 = PILImage.fromarray(t2)

                inputs = processor(images=(pil1, pil2), return_tensors="pt")
                inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

                outputs = model(**inputs)
                logits = outputs.logits
                prob_map = _logits_to_change_prob(logits, torch).cpu().numpy()

                out_h, out_w = prob_map.shape
                if out_h != tile or out_w != tile:
                    prob_map = cv2.resize(prob_map, (tile, tile),
                                          interpolation=cv2.INTER_LINEAR)

                score_sum[y0:y0+tile, x0:x0+tile] += prob_map * weight_2d
                count[y0:y0+tile, x0:x0+tile] += weight_2d

    count = np.maximum(count, 1e-6)
    avg_score = score_sum / count
    return avg_score[:h, :w]


def predict_change_mask(img1, img2, threshold=0.5):
    """
    Run AdaptFormer inference on two RGB numpy arrays (H, W, 3).
    Averages predictions over test-time augmentation flips (DETECTION_TTA) for
    higher-accuracy, less boundary-sensitive change maps.
    Returns (uint8 mask [0 or 255], float32 score map [0-1]).
    Use threshold > 1.0 to obtain score map only (empty mask).
    """
    _load_model()

    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h, w = img1.shape[:2]
    ops = _resolve_tta_ops()

    acc = np.zeros((h, w), dtype=np.float32)
    n = 0
    for op in ops:
        a1 = np.ascontiguousarray(_apply_flip(img1, op))
        a2 = np.ascontiguousarray(_apply_flip(img2, op))
        try:
            s = _infer_score_map(a1, a2)
        except Exception as exc:
            logger.warning("TTA pass %s failed: %s", op, exc)
            continue
        acc += _unflip_map(s, op)
        n += 1

    if n == 0:
        raise RuntimeError("AdaptFormer inference produced no predictions")
    avg_score = acc / float(n)

    mask = (avg_score >= threshold).astype(np.uint8) * 255
    return mask, avg_score
