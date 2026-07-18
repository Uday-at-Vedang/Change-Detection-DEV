"""KPCAMNet-style unsupervised deep change features (Kernel-PCA convolution).

Implements the core idea of "Unsupervised Change Detection in Multitemporal
VHR Images Based on Deep Kernel PCA Convolutional Mapping Network"
(Wu, Chen et al., IEEE TCYB 2022, https://github.com/ChenHongruixuan/KPCAMNet):

1. Patch vectors from both timestamps are projected through *shared* kernel-PCA
   mappings (siamese: identical projection for both images), stacked in layers
   like a small convolutional network. No labels or pretraining required —
   projections are fitted on the image pair itself.
2. The per-pixel feature difference is mapped to a polar domain:
   magnitude ``rho`` (change strength) and direction ``theta`` (change type).
3. ``rho`` is thresholded (Otsu + hysteresis by the caller) for the binary
   change mask; ``theta`` supports discriminating change types downstream.

For CPU practicality the exact KPCA (O(N^2) kernel matrix) is replaced by the
standard Nystroem approximation + linear PCA, which scales linearly in pixels
while preserving the kernelized mapping. Layer projections are fitted on a
random pixel sample from BOTH images so the mapping is shared (siamese).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

_log = logging.getLogger(__name__)


@dataclass
class KPCAChangeResult:
    rho: np.ndarray            # float32 [0,1], change magnitude, input resolution
    theta: np.ndarray          # float32 [-pi,pi], change direction, input resolution
    n_layers: int
    n_components: int
    analysis_shape: Tuple[int, int]


def _patch_stack(img: np.ndarray, patch: int) -> np.ndarray:
    """(H,W,C) -> (H,W,C*patch*patch) neighborhood features via shifted views."""
    h, w, c = img.shape
    r = patch // 2
    padded = cv2.copyMakeBorder(img, r, r, r, r, cv2.BORDER_REFLECT)
    views = []
    for dy in range(patch):
        for dx in range(patch):
            views.append(padded[dy:dy + h, dx:dx + w])
    return np.concatenate(views, axis=2).astype(np.float32)


def _fit_shared_projection(feat_a: np.ndarray, feat_b: np.ndarray,
                           n_components: int, sample: int, seed: int):
    """Fit Nystroem(RBF) + PCA on a pixel sample drawn from both images."""
    from sklearn.decomposition import PCA
    from sklearn.kernel_approximation import Nystroem
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    rng = np.random.default_rng(seed)
    flat = np.concatenate([
        feat_a.reshape(-1, feat_a.shape[2]),
        feat_b.reshape(-1, feat_b.shape[2]),
    ], axis=0)
    take = min(sample, flat.shape[0])
    idx = rng.choice(flat.shape[0], size=take, replace=False)
    fit_data = flat[idx]

    n_landmarks = int(min(128, max(32, take // 24)))
    pipe = Pipeline([
        ("scale", StandardScaler()),
        ("nystroem", Nystroem(kernel="rbf", n_components=n_landmarks, random_state=seed)),
        ("pca", PCA(n_components=n_components, random_state=seed)),
    ])
    pipe.fit(fit_data)
    return pipe


def _transform_image(pipe, feat: np.ndarray) -> np.ndarray:
    h, w, c = feat.shape
    out = pipe.transform(feat.reshape(-1, c))
    return out.reshape(h, w, -1).astype(np.float32)


def compute_kpca_change(
    img1: np.ndarray,
    img2: np.ndarray,
    *,
    n_components: int = 8,
    n_layers: int = 2,
    patch: int = 5,
    max_side: int = 768,
    fit_sample: int = 4000,
    seed: int = 42,
) -> Optional[KPCAChangeResult]:
    """Siamese KPCA change features for an RGB image pair.

    Returns rho/theta maps at the input resolution, or None on failure
    (caller falls back to its previous behavior).
    """
    try:
        if img1.shape != img2.shape or img1.ndim != 3:
            return None
        full_h, full_w = img1.shape[:2]

        # Bounded analysis resolution keeps the Nystroem transform CPU-friendly
        scale = min(1.0, max_side / max(full_h, full_w))
        if scale < 1.0:
            ah, aw = max(64, int(full_h * scale)), max(64, int(full_w * scale))
            a = cv2.resize(img1, (aw, ah), interpolation=cv2.INTER_AREA)
            b = cv2.resize(img2, (aw, ah), interpolation=cv2.INTER_AREA)
        else:
            a, b = img1, img2

        a = a.astype(np.float32) / 255.0
        b = b.astype(np.float32) / 255.0

        feat_a, feat_b = a, b
        layer_patch = patch
        for layer in range(max(1, n_layers)):
            stack_a = _patch_stack(feat_a, layer_patch)
            stack_b = _patch_stack(feat_b, layer_patch)
            pipe = _fit_shared_projection(
                stack_a, stack_b, n_components=n_components,
                sample=fit_sample, seed=seed + layer,
            )
            feat_a = _transform_image(pipe, stack_a)
            feat_b = _transform_image(pipe, stack_b)
            layer_patch = 3  # deeper layers use tighter neighborhoods

        diff = feat_a - feat_b

        # Polar mapping (KPCAMNet): magnitude over all components; direction
        # from the two most-informative difference components.
        rho = np.sqrt(np.sum(diff * diff, axis=2))
        var_order = np.argsort(np.var(diff.reshape(-1, diff.shape[2]), axis=0))[::-1]
        d1 = diff[:, :, var_order[0]]
        d2 = diff[:, :, var_order[1]] if diff.shape[2] > 1 else np.zeros_like(d1)
        theta = np.arctan2(d2, d1)

        # Robust [0,1] normalization of magnitude
        hi = float(np.quantile(rho, 0.995))
        if hi <= 1e-8:
            hi = float(rho.max() + 1e-8)
        rho = np.clip(rho / hi, 0.0, 1.0).astype(np.float32)

        if rho.shape != (full_h, full_w):
            rho = cv2.resize(rho, (full_w, full_h), interpolation=cv2.INTER_LINEAR)
            theta = cv2.resize(theta.astype(np.float32), (full_w, full_h),
                               interpolation=cv2.INTER_NEAREST)

        return KPCAChangeResult(
            rho=rho.astype(np.float32),
            theta=theta.astype(np.float32),
            n_layers=max(1, n_layers),
            n_components=n_components,
            analysis_shape=(a.shape[0], a.shape[1]),
        )
    except Exception as exc:
        _log.warning("KPCA change features failed (%s) — caller will fall back", exc)
        return None
