"""
Siamese U-Net inference for satellite change detection.

Loads a TorchScript model exported from the training notebook and runs
tile-based inference on arbitrary-size image pairs, producing a binary
change mask compatible with the rest of the detection pipeline.

Set CHANGE_MODEL_PATH env var to the .pt file location.
Falls back to the rule-based AI fusion when no model is available.
"""
import logging
import os
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_MODEL = None
_MODEL_PATH = os.environ.get("CHANGE_MODEL_PATH", "data/siamese_unet.pt")
_TILE_SIZE = 256
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _get_torch():
    """Lazy import torch — only when model exists."""
    try:
        import torch
        return torch
    except ImportError:
        return None


def is_model_available():
    """Check if a trained model file exists and torch is installed."""
    return Path(_MODEL_PATH).is_file() and _get_torch() is not None


def _load_model():
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    torch = _get_torch()
    if torch is None:
        raise RuntimeError("PyTorch is not installed")
    path = Path(_MODEL_PATH)
    if not path.is_file():
        raise FileNotFoundError(f"Model not found at {path}")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    _MODEL = torch.jit.load(str(path), map_location=device)
    _MODEL.eval()
    logger.info("Loaded Siamese U-Net from %s on %s", path, device)
    return _MODEL


def _preprocess_tile(tile):
    """Normalize a (H, W, 3) uint8 RGB tile to (1, 3, H, W) float tensor."""
    torch = _get_torch()
    img = tile.astype(np.float32) / 255.0
    img = (img - _MEAN) / _STD
    tensor = torch.from_numpy(img.transpose(2, 0, 1)).unsqueeze(0)
    return tensor


def predict_change_mask(img1, img2, threshold=0.5):
    """
    Run Siamese U-Net inference on two RGB numpy arrays (H, W, 3).
    Images are split into overlapping tiles, predicted individually,
    and stitched back into a full-resolution binary mask.

    Returns a uint8 mask (0 or 255) at the input resolution.
    """
    torch = _get_torch()
    model = _load_model()
    device = next(model.parameters()).device

    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h, w = img1.shape[:2]
    tile = _TILE_SIZE
    stride = tile * 3 // 4  # 75% overlap for smoother stitching

    # Pad to make dimensions divisible by tile size
    pad_h = (tile - h % tile) % tile
    pad_w = (tile - w % tile) % tile
    if pad_h or pad_w:
        img1 = np.pad(img1, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
        img2 = np.pad(img2, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    ph, pw = img1.shape[:2]
    score_sum = np.zeros((ph, pw), dtype=np.float32)
    count = np.zeros((ph, pw), dtype=np.float32)

    with torch.no_grad():
        for y0 in range(0, ph - tile + 1, stride):
            for x0 in range(0, pw - tile + 1, stride):
                t1 = _preprocess_tile(img1[y0:y0+tile, x0:x0+tile])
                t2 = _preprocess_tile(img2[y0:y0+tile, x0:x0+tile])
                logits = model(t1.to(device), t2.to(device))
                prob = torch.sigmoid(logits).squeeze().cpu().numpy()
                score_sum[y0:y0+tile, x0:x0+tile] += prob
                count[y0:y0+tile, x0:x0+tile] += 1.0

    count = np.maximum(count, 1.0)
    avg_score = score_sum / count

    # Crop back to original size
    avg_score = avg_score[:h, :w]

    mask = (avg_score >= threshold).astype(np.uint8) * 255
    return mask, avg_score
