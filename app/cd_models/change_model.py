"""
Siamese U-Net for satellite change detection.

A lightweight Siamese encoder shares weights between before/after images,
fuses features via concatenation + difference, and decodes into a binary
change probability map.

Designed for CPU inference (< 2s per 256x256 tile).
"""
import logging
import os
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_MODEL = None
_DEVICE = None
_AVAILABLE = None
_WEIGHTS_DIR = Path(__file__).parent / "weights"
_WEIGHTS_FILE = _WEIGHTS_DIR / "siamese_unet_cd.pt"


def _try_torch():
    try:
        import torch
        import torch.nn as nn
        return torch, nn
    except ImportError:
        return None, None


# ---------------------------------------------------------------------------
# Model architecture
# ---------------------------------------------------------------------------

def _build_model():
    torch, nn = _try_torch()
    if torch is None:
        return None

    class ConvBlock(nn.Module):
        def __init__(self, in_ch, out_ch):
            super().__init__()
            self.block = nn.Sequential(
                nn.Conv2d(in_ch, out_ch, 3, padding=1, bias=False),
                nn.BatchNorm2d(out_ch),
                nn.ReLU(inplace=True),
                nn.Conv2d(out_ch, out_ch, 3, padding=1, bias=False),
                nn.BatchNorm2d(out_ch),
                nn.ReLU(inplace=True),
            )
        def forward(self, x):
            return self.block(x)

    class Encoder(nn.Module):
        def __init__(self, in_ch=3, base=32):
            super().__init__()
            self.enc1 = ConvBlock(in_ch, base)
            self.enc2 = ConvBlock(base, base * 2)
            self.enc3 = ConvBlock(base * 2, base * 4)
            self.enc4 = ConvBlock(base * 4, base * 8)
            self.pool = nn.MaxPool2d(2)

        def forward(self, x):
            e1 = self.enc1(x)
            e2 = self.enc2(self.pool(e1))
            e3 = self.enc3(self.pool(e2))
            e4 = self.enc4(self.pool(e3))
            return [e1, e2, e3, e4]

    class SiameseUNet(nn.Module):
        """
        Siamese U-Net: shared encoder processes before/after images independently.
        Decoder fuses features via concatenation of both streams + their absolute
        difference, providing the decoder with explicit change information.
        """
        def __init__(self, in_ch=3, base=32, out_ch=2):
            super().__init__()
            self.encoder = Encoder(in_ch, base)
            b = base

            # Decoder: at each level receives [enc_a, enc_b, |enc_a-enc_b|] = 3x channels
            self.up4 = nn.ConvTranspose2d(b * 8, b * 4, 2, stride=2)
            self.dec4 = ConvBlock(b * 4 + b * 4 * 3, b * 4)

            self.up3 = nn.ConvTranspose2d(b * 4, b * 2, 2, stride=2)
            self.dec3 = ConvBlock(b * 2 + b * 2 * 3, b * 2)

            self.up2 = nn.ConvTranspose2d(b * 2, b, 2, stride=2)
            self.dec2 = ConvBlock(b + b * 3, b)

            self.head = nn.Conv2d(b, out_ch, 1)

        def forward(self, img_a, img_b):
            feats_a = self.encoder(img_a)
            feats_b = self.encoder(img_b)

            # Bottleneck: fuse deepest features
            bot = torch.cat([feats_a[3], feats_b[3], torch.abs(feats_a[3] - feats_b[3])], dim=1)

            import torch.nn.functional as F
            # Level 3
            d4 = self.up4(feats_a[3])
            skip3 = torch.cat([feats_a[2], feats_b[2], torch.abs(feats_a[2] - feats_b[2])], dim=1)
            d4 = self.dec4(torch.cat([d4, skip3], dim=1))

            # Level 2
            d3 = self.up3(d4)
            skip2 = torch.cat([feats_a[1], feats_b[1], torch.abs(feats_a[1] - feats_b[1])], dim=1)
            d3 = self.dec3(torch.cat([d3, skip2], dim=1))

            # Level 1
            d2 = self.up2(d3)
            skip1 = torch.cat([feats_a[0], feats_b[0], torch.abs(feats_a[0] - feats_b[0])], dim=1)
            d2 = self.dec2(torch.cat([d2, skip1], dim=1))

            return self.head(d2)

    return SiameseUNet


# ---------------------------------------------------------------------------
# Model loading (singleton)
# ---------------------------------------------------------------------------

def has_siamese_weights():
    """True only when a trained weights file is present."""
    return _WEIGHTS_FILE.is_file()


def is_siamese_available():
    """PyTorch installed and pretrained weights available."""
    global _AVAILABLE
    if _AVAILABLE is not None:
        return _AVAILABLE
    torch, _ = _try_torch()
    _AVAILABLE = torch is not None and has_siamese_weights()
    return _AVAILABLE


def _load_siamese():
    global _MODEL, _DEVICE
    if _MODEL is not None:
        return _MODEL

    torch, _ = _try_torch()
    if torch is None:
        raise RuntimeError("PyTorch not installed")

    _DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    ModelClass = _build_model()
    model = ModelClass(in_ch=3, base=32, out_ch=2)

    if _WEIGHTS_FILE.exists():
        logger.info("Loading Siamese U-Net weights from %s", _WEIGHTS_FILE)
        state = torch.load(str(_WEIGHTS_FILE), map_location=_DEVICE, weights_only=True)
        model.load_state_dict(state)
    else:
        logger.info("No pretrained weights found at %s — using random init "
                     "(model will still produce change maps but accuracy depends on "
                     "classical fusion weighting)", _WEIGHTS_FILE)

    model.to(_DEVICE)
    model.eval()
    _MODEL = model
    return _MODEL


# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

_TILE = 256


def predict_siamese(img1, img2, threshold=0.5):
    """
    Run Siamese U-Net inference on two RGB uint8 arrays.
    Tile-based with overlap stitching (same pattern as AdaptFormer).
    Returns (uint8 mask [0|255], float32 probability map [0-1]).
    """
    torch, _ = _try_torch()
    model = _load_siamese()

    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h, w = img1.shape[:2]
    tile = _TILE
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

    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)

    with torch.no_grad():
        for y0 in range(0, ph - tile + 1, stride):
            for x0 in range(0, pw - tile + 1, stride):
                t1 = img1[y0:y0+tile, x0:x0+tile].astype(np.float32) / 255.0
                t2 = img2[y0:y0+tile, x0:x0+tile].astype(np.float32) / 255.0

                t1 = (t1 - mean) / std
                t2 = (t2 - mean) / std

                ta = torch.from_numpy(t1.transpose(2, 0, 1)).unsqueeze(0).to(_DEVICE)
                tb = torch.from_numpy(t2.transpose(2, 0, 1)).unsqueeze(0).to(_DEVICE)

                logits = model(ta, tb)
                probs = torch.softmax(logits, dim=1)
                prob_map = probs[0, 1].cpu().numpy()

                if prob_map.shape != (tile, tile):
                    prob_map = cv2.resize(prob_map, (tile, tile))

                score_sum[y0:y0+tile, x0:x0+tile] += prob_map * weight_2d
                count[y0:y0+tile, x0:x0+tile] += weight_2d

    count = np.maximum(count, 1e-6)
    avg = score_sum / count
    avg = avg[:h, :w]

    mask = (avg >= threshold).astype(np.uint8) * 255
    return mask, avg
