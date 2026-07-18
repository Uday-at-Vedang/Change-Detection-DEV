"""BIT_CD: Bitemporal Image Transformer for change detection (vendored).

Self-contained port of the ``base_transformer_pos_s4_dd8_dedim8`` network from
https://github.com/justchenhao/BIT_CD (Chen et al., "Remote Sensing Image
Change Detection with Transformers", TGRS 2021), rewritten without the einops
dependency and using the torchvision ResNet18 backbone.

Weights are NOT bundled. Place a LEVIR-CD checkpoint at
``app/cd_models/weights/bit_cd_levir.pth`` (or point ``BIT_CD_WEIGHTS`` at it).
Both the raw training checkpoint (``model_G_state_dict`` key, from the
official repo's best_ckpt.pt) and a plain converted state_dict are accepted.

Used by ``model_inference`` as an optional second model ensembled with
AdaptFormer when ``DETECTION_ENSEMBLE=on``.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

_BIT_MODEL = None
_BIT_DEVICE = None
_BIT_FAILED = False

WEIGHTS_DIR = Path(__file__).resolve().parent / "weights"
DEFAULT_WEIGHTS = WEIGHTS_DIR / "bit_cd_levir.pth"
TILE_SIZE = 256  # LEVIR-CD native patch size


def weights_path() -> Path:
    env = os.environ.get("BIT_CD_WEIGHTS", "").strip()
    return Path(env) if env else DEFAULT_WEIGHTS


def weights_available() -> bool:
    p = weights_path()
    return p.is_file() and p.stat().st_size > 1_000_000


def _build_modules():
    import torch
    from torch import nn

    class TwoLayerConv2d(nn.Sequential):
        def __init__(self, in_channels, out_channels, kernel_size=3):
            super().__init__(
                nn.Conv2d(in_channels, in_channels, kernel_size=kernel_size,
                          padding=kernel_size // 2, stride=1, bias=False),
                nn.BatchNorm2d(in_channels),
                nn.ReLU(),
                nn.Conv2d(in_channels, out_channels, kernel_size=kernel_size,
                          padding=kernel_size // 2, stride=1),
            )

    class Residual(nn.Module):
        def __init__(self, fn):
            super().__init__()
            self.fn = fn

        def forward(self, x, **kw):
            return self.fn(x, **kw) + x

    class Residual2(nn.Module):
        def __init__(self, fn):
            super().__init__()
            self.fn = fn

        def forward(self, x, m, **kw):
            return self.fn(x, m, **kw) + x

    class PreNorm(nn.Module):
        def __init__(self, dim, fn):
            super().__init__()
            self.norm = nn.LayerNorm(dim)
            self.fn = fn

        def forward(self, x, **kw):
            return self.fn(self.norm(x), **kw)

    class PreNorm2(nn.Module):
        def __init__(self, dim, fn):
            super().__init__()
            self.norm = nn.LayerNorm(dim)
            self.fn = fn

        def forward(self, x, m, **kw):
            return self.fn(self.norm(x), self.norm(m), **kw)

    class FeedForward(nn.Module):
        def __init__(self, dim, hidden_dim):
            super().__init__()
            self.net = nn.Sequential(
                nn.Linear(dim, hidden_dim), nn.GELU(), nn.Dropout(0.0),
                nn.Linear(hidden_dim, dim), nn.Dropout(0.0),
            )

        def forward(self, x):
            return self.net(x)

    def _split_heads(t, heads):
        b, n, hd = t.shape
        d = hd // heads
        return t.view(b, n, heads, d).permute(0, 2, 1, 3)

    def _merge_heads(t):
        b, h, n, d = t.shape
        return t.permute(0, 2, 1, 3).reshape(b, n, h * d)

    class Attention(nn.Module):
        def __init__(self, dim, heads=8, dim_head=64):
            super().__init__()
            inner = dim_head * heads
            self.heads = heads
            self.scale = dim ** -0.5
            self.to_qkv = nn.Linear(dim, inner * 3, bias=False)
            self.to_out = nn.Sequential(nn.Linear(inner, dim), nn.Dropout(0.0))

        def forward(self, x):
            q, k, v = self.to_qkv(x).chunk(3, dim=-1)
            q, k, v = (_split_heads(t, self.heads) for t in (q, k, v))
            attn = (q @ k.transpose(-1, -2) * self.scale).softmax(dim=-1)
            return self.to_out(_merge_heads(attn @ v))

    class CrossAttention(nn.Module):
        def __init__(self, dim, heads=8, dim_head=64, softmax=True):
            super().__init__()
            inner = dim_head * heads
            self.heads = heads
            self.scale = dim ** -0.5
            self.softmax = softmax
            self.to_q = nn.Linear(dim, inner, bias=False)
            self.to_k = nn.Linear(dim, inner, bias=False)
            self.to_v = nn.Linear(dim, inner, bias=False)
            self.to_out = nn.Sequential(nn.Linear(inner, dim), nn.Dropout(0.0))

        def forward(self, x, m):
            q = _split_heads(self.to_q(x), self.heads)
            k = _split_heads(self.to_k(m), self.heads)
            v = _split_heads(self.to_v(m), self.heads)
            dots = q @ k.transpose(-1, -2) * self.scale
            attn = dots.softmax(dim=-1) if self.softmax else dots
            return self.to_out(_merge_heads(attn @ v))

    class Transformer(nn.Module):
        def __init__(self, dim, depth, heads, dim_head, mlp_dim):
            super().__init__()
            self.layers = nn.ModuleList([
                nn.ModuleList([
                    Residual(PreNorm(dim, Attention(dim, heads, dim_head))),
                    Residual(PreNorm(dim, FeedForward(dim, mlp_dim))),
                ]) for _ in range(depth)
            ])

        def forward(self, x):
            for attn, ff in self.layers:
                x = attn(x)
                x = ff(x)
            return x

    class TransformerDecoder(nn.Module):
        def __init__(self, dim, depth, heads, dim_head, mlp_dim, softmax=True):
            super().__init__()
            self.layers = nn.ModuleList([
                nn.ModuleList([
                    Residual2(PreNorm2(dim, CrossAttention(dim, heads, dim_head, softmax))),
                    Residual(PreNorm(dim, FeedForward(dim, mlp_dim))),
                ]) for _ in range(depth)
            ])

        def forward(self, x, m):
            for attn, ff in self.layers:
                x = attn(x, m)
                x = ff(x)
            return x

    class DilatedBasicBlock(nn.Module):
        """torchvision BasicBlock with dilation allowed (as in BIT_CD's resnet)."""
        expansion = 1

        def __init__(self, inplanes, planes, stride=1, downsample=None, dilation=1):
            super().__init__()
            self.conv1 = nn.Conv2d(inplanes, planes, 3, stride=stride,
                                   padding=dilation, dilation=dilation, bias=False)
            self.bn1 = nn.BatchNorm2d(planes)
            self.relu = nn.ReLU(inplace=True)
            self.conv2 = nn.Conv2d(planes, planes, 3, padding=dilation,
                                   dilation=dilation, bias=False)
            self.bn2 = nn.BatchNorm2d(planes)
            self.downsample = downsample

        def forward(self, x):
            identity = x
            out = self.relu(self.bn1(self.conv1(x)))
            out = self.bn2(self.conv2(out))
            if self.downsample is not None:
                identity = self.downsample(x)
            return self.relu(out + identity)

    class DilatedResNet18(nn.Module):
        """ResNet18 trunk with replace_stride_with_dilation=[False, True, True].

        Same module naming as torchvision so BIT_CD checkpoints load directly.
        """

        def __init__(self):
            super().__init__()
            self.inplanes = 64
            self.dilation = 1
            self.conv1 = nn.Conv2d(3, 64, 7, stride=2, padding=3, bias=False)
            self.bn1 = nn.BatchNorm2d(64)
            self.relu = nn.ReLU(inplace=True)
            self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
            # replace_stride_with_dilation=[False, True, True]: layer2 keeps
            # stride 2; layers 3-4 trade stride for dilation.
            self.layer1 = self._make_layer(64, 2, stride=1, dilate=False)
            self.layer2 = self._make_layer(128, 2, stride=2, dilate=False)
            self.layer3 = self._make_layer(256, 2, stride=2, dilate=True)
            self.layer4 = self._make_layer(512, 2, stride=2, dilate=True)

        def _make_layer(self, planes, blocks, stride, dilate):
            downsample = None
            previous_dilation = self.dilation
            if dilate:
                self.dilation *= stride
                stride = 1
            if stride != 1 or self.inplanes != planes:
                downsample = nn.Sequential(
                    nn.Conv2d(self.inplanes, planes, 1, stride=stride, bias=False),
                    nn.BatchNorm2d(planes),
                )
            layers = [DilatedBasicBlock(self.inplanes, planes, stride,
                                        downsample, previous_dilation)]
            self.inplanes = planes
            for _ in range(1, blocks):
                layers.append(DilatedBasicBlock(planes, planes, dilation=self.dilation))
            return nn.Sequential(*layers)

    class BITTransformer(nn.Module):
        """ResNet18 (4 stages, dilated) + token transformer + difference head."""

        def __init__(self, output_nc=2, token_len=4, enc_depth=1, dec_depth=8,
                     dim_head=64, decoder_dim_head=8):
            super().__init__()
            self.resnet = DilatedResNet18()
            self.relu = nn.ReLU()
            self.upsamplex2 = nn.Upsample(scale_factor=2)
            self.upsamplex4 = nn.Upsample(scale_factor=4, mode="bilinear")
            self.classifier = TwoLayerConv2d(in_channels=32, out_channels=output_nc)
            self.conv_pred = nn.Conv2d(256, 32, kernel_size=3, padding=1)

            self.token_len = token_len
            dim = 32
            self.conv_a = nn.Conv2d(dim, token_len, kernel_size=1, padding=0, bias=False)
            self.pos_embedding = nn.Parameter(torch.randn(1, token_len * 2, dim))
            self.transformer = Transformer(dim, enc_depth, 8, dim_head, 2 * dim)
            self.transformer_decoder = TransformerDecoder(
                dim, dec_depth, 8, decoder_dim_head, 2 * dim, softmax=True)

        def forward_single(self, x):
            x = self.resnet.conv1(x)
            x = self.resnet.bn1(x)
            x = self.resnet.relu(x)
            x = self.resnet.maxpool(x)
            x = self.resnet.layer1(x)
            x = self.resnet.layer2(x)
            x = self.resnet.layer3(x)  # stages_num=4 -> stop before layer4
            x = self.upsamplex2(x)
            return self.conv_pred(x)

        def _semantic_tokens(self, x):
            b, c, h, w = x.shape
            att = self.conv_a(x).view(b, self.token_len, -1).softmax(dim=-1)
            flat = x.view(b, c, -1)
            return torch.einsum("bln,bcn->blc", att, flat)

        def _decode(self, x, m):
            b, c, h, w = x.shape
            seq = x.flatten(2).transpose(1, 2)
            seq = self.transformer_decoder(seq, m)
            return seq.transpose(1, 2).reshape(b, c, h, w)

        def forward(self, x1, x2):
            x1 = self.forward_single(x1)
            x2 = self.forward_single(x2)
            t1 = self._semantic_tokens(x1)
            t2 = self._semantic_tokens(x2)
            tokens = torch.cat([t1, t2], dim=1) + self.pos_embedding
            tokens = self.transformer(tokens)
            t1, t2 = tokens.chunk(2, dim=1)
            x1 = self._decode(x1, t1)
            x2 = self._decode(x2, t2)
            x = torch.abs(x1 - x2)
            x = self.upsamplex4(x)
            return self.classifier(x)

    return BITTransformer


def _extract_state_dict(checkpoint) -> dict:
    if isinstance(checkpoint, dict):
        for key in ("model_G_state_dict", "state_dict", "model_state_dict"):
            if key in checkpoint and isinstance(checkpoint[key], dict):
                checkpoint = checkpoint[key]
                break
    return {k[7:] if k.startswith("module.") else k: v for k, v in checkpoint.items()}


def load_bit_model():
    """Load BIT_CD with LEVIR weights. Returns None when unavailable."""
    global _BIT_MODEL, _BIT_DEVICE, _BIT_FAILED
    if _BIT_MODEL is not None:
        return _BIT_MODEL
    if _BIT_FAILED or not weights_available():
        return None
    try:
        import torch

        BITTransformer = _build_modules()
        model = BITTransformer()
        checkpoint = torch.load(weights_path(), map_location="cpu", weights_only=False)
        state = _extract_state_dict(checkpoint)
        missing, unexpected = model.load_state_dict(state, strict=False)
        real_missing = [k for k in missing if "num_batches_tracked" not in k]
        if len(real_missing) > 20:
            raise RuntimeError(
                f"checkpoint mismatch: {len(real_missing)} missing keys "
                f"(e.g. {real_missing[:4]}), {len(unexpected)} unexpected")
        if real_missing or unexpected:
            logger.warning("BIT_CD loaded with %d missing / %d unexpected keys",
                           len(real_missing), len(unexpected))
        _BIT_DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model.to(_BIT_DEVICE)
        model.eval()
        _BIT_MODEL = model
        logger.info("BIT_CD loaded from %s on %s", weights_path(), _BIT_DEVICE)
        return _BIT_MODEL
    except Exception as exc:
        _BIT_FAILED = True
        logger.error("BIT_CD load failed: %s", exc)
        return None


def bit_score_map(img1: np.ndarray, img2: np.ndarray) -> Optional[np.ndarray]:
    """Tiled BIT_CD change probability map in [0,1] at (h, w), or None."""
    model = load_bit_model()
    if model is None:
        return None
    try:
        import torch

        from .model_utils import tiled_score_map

        # ImageNet-style normalization matching the BIT_CD training transforms
        mean = np.array([0.5, 0.5, 0.5], dtype=np.float32)
        std = np.array([0.5, 0.5, 0.5], dtype=np.float32)

        def _to_tensor(arr):
            t = (arr.astype(np.float32) / 255.0 - mean) / std
            return torch.from_numpy(t.transpose(2, 0, 1)).unsqueeze(0).to(_BIT_DEVICE)

        def _score_tile(t1, t2):
            with torch.no_grad():
                logits = model(_to_tensor(t1), _to_tensor(t2))
                prob = torch.softmax(logits, dim=1)[0, 1]
            return prob.cpu().numpy().astype(np.float32)

        return tiled_score_map(_score_tile, img1, img2,
                               tile_size=TILE_SIZE, overlap=TILE_SIZE // 4)
    except Exception as exc:
        logger.warning("BIT_CD scoring failed: %s", exc)
        return None


def bit_status() -> dict:
    return {
        "weightsPath": str(weights_path()),
        "weightsAvailable": weights_available(),
        "loaded": _BIT_MODEL is not None,
        "loadFailed": _BIT_FAILED,
    }
