"""
KPCA-based unsupervised change detection (KPCAMNet-inspired).

Fits Kernel PCA filters on randomly sampled patches from both images (weight-
shared/siamese, no labels needed), projects the whole image through those
filters, then detects change via the L2 norm of the feature difference. This
is a single-stage reconstruction of the described KPCAMNet algorithm (not a
port of the original repo's code, which isn't part of this project) — scoped
down for CPU feasibility: bounded processing resolution and a modest training
patch count, since KernelPCA.transform cost scales with
n_query_patches x n_train_patches.
"""
import cv2
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from sklearn.decomposition import KernelPCA


def _extract_all_patches(img: np.ndarray, patch_size: int) -> np.ndarray:
    """Every overlapping patch_size x patch_size patch, one per pixel (reflect-padded)."""
    half = patch_size // 2
    padded = np.pad(img, half, mode="reflect")
    windows = sliding_window_view(padded, (patch_size, patch_size))
    h, w = img.shape
    return windows.reshape(h, w, -1)


def _project_in_chunks(kpca: KernelPCA, flat_patches: np.ndarray, row_pixels: int,
                       n_components: int) -> np.ndarray:
    """KernelPCA.transform in row-chunks so memory stays bounded on large images."""
    chunks = []
    step = max(1, 200_000 // max(1, row_pixels)) * row_pixels
    for i in range(0, flat_patches.shape[0], step):
        chunks.append(kpca.transform(flat_patches[i:i + step]))
    return np.concatenate(chunks, axis=0)


def kpca_change_mask(img1: np.ndarray, img2: np.ndarray, patch_size: int = 5,
                     n_components: int = 8, gamma: float = 5e-4,
                     n_train_patches: int = 300, max_side: int = 768):
    """
    Returns (magnitude_map float32 [0,1] at processed resolution, debug dict).
    Caller handles thresholding/cleanup/resize-back so this stays testable in
    isolation without pulling in the rest of the detection pipeline.
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    h0, w0 = img1.shape[:2]
    scale = min(1.0, max_side / max(h0, w0, 1))
    if scale < 1.0:
        nh, nw = max(1, int(h0 * scale)), max(1, int(w0 * scale))
        img1_s = cv2.resize(img1, (nw, nh), interpolation=cv2.INTER_AREA)
        img2_s = cv2.resize(img2, (nw, nh), interpolation=cv2.INTER_AREA)
    else:
        img1_s, img2_s = img1, img2

    gray1 = cv2.cvtColor(img1_s, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    gray2 = cv2.cvtColor(img2_s, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
    # Per-image z-score standardization (matches the described preprocessing)
    gray1 = (gray1 - gray1.mean()) / (gray1.std() + 1e-8)
    gray2 = (gray2 - gray2.mean()) / (gray2.std() + 1e-8)

    h, w = gray1.shape
    patches1 = _extract_all_patches(gray1, patch_size)
    patches2 = _extract_all_patches(gray2, patch_size)
    flat1 = patches1.reshape(-1, patches1.shape[-1])
    flat2 = patches2.reshape(-1, patches2.shape[-1])

    rng = np.random.default_rng(42)
    half_n = max(1, n_train_patches // 2)
    idx1 = rng.choice(flat1.shape[0], size=min(half_n, flat1.shape[0]), replace=False)
    idx2 = rng.choice(flat2.shape[0], size=min(half_n, flat2.shape[0]), replace=False)
    train_patches = np.concatenate([flat1[idx1], flat2[idx2]], axis=0)

    n_components_eff = max(1, min(n_components, train_patches.shape[0] - 1))
    kpca = KernelPCA(n_components=n_components_eff, kernel="rbf", gamma=gamma)
    kpca.fit(train_patches)

    feat1 = _project_in_chunks(kpca, flat1, w, n_components_eff).reshape(h, w, n_components_eff)
    feat2 = _project_in_chunks(kpca, flat2, w, n_components_eff).reshape(h, w, n_components_eff)

    diff = feat1 - feat2
    magnitude = np.linalg.norm(diff, axis=-1)
    mag_norm = magnitude / (magnitude.max() + 1e-8)

    if scale < 1.0:
        mag_norm = cv2.resize(mag_norm.astype(np.float32), (w0, h0), interpolation=cv2.INTER_LINEAR)

    debug = {
        "n_components": int(n_components_eff),
        "n_train_patches": int(train_patches.shape[0]),
        "processedSide": int(max(h, w)),
        "patchSize": int(patch_size),
        "gamma": float(gamma),
    }
    return mag_norm.astype(np.float32), debug
