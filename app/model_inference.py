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


def _try_import():
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModel
        return torch, AutoImageProcessor, AutoModel
    except ImportError:
        return None, None, None


def is_model_available():
    """Check if torch and transformers are installed."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    torch, _, _ = _try_import()
    _AVAILABLE = torch is not None
    return _AVAILABLE


def _load_model():
    global _MODEL, _PROCESSOR, _DEVICE
    if _MODEL is not None:
        return _MODEL, _PROCESSOR

    torch, AutoImageProcessor, AutoModel = _try_import()
    if torch is None:
        raise RuntimeError("PyTorch/transformers not installed")

    _DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    cache_dir = os.environ.get("HF_HOME", None)
    logger.info("Loading AdaptFormer from %s ...", _MODEL_ID)
    _PROCESSOR = AutoImageProcessor.from_pretrained(
        _MODEL_ID, cache_dir=cache_dir, trust_remote_code=True)
    _MODEL = AutoModel.from_pretrained(
        _MODEL_ID, cache_dir=cache_dir, trust_remote_code=True)
    _MODEL.to(_DEVICE)
    _MODEL.eval()
    logger.info("AdaptFormer loaded on %s", _DEVICE)
    return _MODEL, _PROCESSOR


def predict_change_mask(img1, img2, threshold=0.5):
    """
    Run AdaptFormer inference on two RGB numpy arrays (H, W, 3).
    Images are split into overlapping 256x256 tiles (matching LEVIR-CD
    training resolution), predicted individually, and stitched back into
    a full-resolution binary mask.

    Returns (uint8 mask [0 or 255], float32 score map [0-1]).
    """
    torch, _, _ = _try_import()
    model, processor = _load_model()
    from PIL import Image as PILImage

    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h, w = img1.shape[:2]
    tile = _TILE_SIZE
    overlap = tile // 4  # 64px overlap
    stride = tile - overlap  # 192

    pad_h = (tile - h % tile) % tile
    pad_w = (tile - w % tile) % tile
    if pad_h or pad_w:
        img1 = np.pad(img1, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
        img2 = np.pad(img2, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    ph, pw = img1.shape[:2]
    score_sum = np.zeros((ph, pw), dtype=np.float32)
    count = np.zeros((ph, pw), dtype=np.float32)

    # Blending weight: raised-cosine window avoids hard tile boundary seams
    ramp = np.linspace(0, 1, overlap)
    flat = np.ones(tile - 2 * overlap)
    profile = np.concatenate([ramp, flat, ramp[::-1]])
    weight_2d = np.outer(profile, profile).astype(np.float32)

    with torch.no_grad():
        for y0 in range(0, ph - tile + 1, stride):
            for x0 in range(0, pw - tile + 1, stride):
                t1 = img1[y0:y0+tile, x0:x0+tile]
                t2 = img2[y0:y0+tile, x0:x0+tile]

                pil1 = PILImage.fromarray(t1)
                pil2 = PILImage.fromarray(t2)

                inputs = processor(images=(pil1, pil2), return_tensors="pt")
                inputs = {k: v.to(_DEVICE) for k, v in inputs.items()}

                outputs = model(**inputs)
                logits = outputs.logits
                probs = torch.softmax(logits, dim=1)

                prob_map = probs[0, 1].cpu().numpy()

                out_h, out_w = prob_map.shape
                if out_h != tile or out_w != tile:
                    prob_map = cv2.resize(prob_map, (tile, tile),
                                          interpolation=cv2.INTER_LINEAR)

                score_sum[y0:y0+tile, x0:x0+tile] += prob_map * weight_2d
                count[y0:y0+tile, x0:x0+tile] += weight_2d

    count = np.maximum(count, 1e-6)
    avg_score = score_sum / count
    avg_score = avg_score[:h, :w]

    mask = (avg_score >= threshold).astype(np.uint8) * 255
    return mask, avg_score
