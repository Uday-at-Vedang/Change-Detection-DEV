"""IR-MAD: Iteratively Reweighted Multivariate Alteration Detection.

Classical unsupervised change detection statistic (Nielsen 2007), listed among
the strongest traditional methods in the Change-Detection-Review repository
(https://github.com/MinZHANG-WHU/Change-Detection-Review).

MAD finds paired linear combinations of the two images' bands (via canonical
correlation analysis) whose differences maximally decorrelate; the chi-square
sum of squared standardized MAD variates measures change. The IR step
re-estimates the statistics using no-change weights so genuine changes don't
bias the transform, sharpening separation between change and no-change.

Pure numpy — no extra dependencies. Runs on a bounded-resolution copy for CPU
practicality and returns both the change probability map and the final
no-change weights (useful for regression-based radiometric normalization).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np

_log = logging.getLogger(__name__)


@dataclass
class IRMADResult:
    chi2: np.ndarray        # float32, raw chi-square statistic (analysis res)
    change_prob: np.ndarray  # float32 [0,1], at the input resolution
    no_change_weights: np.ndarray  # float32 [0,1], at the input resolution
    iterations: int
    converged: bool


def _weighted_stats(x: np.ndarray, y: np.ndarray, w: np.ndarray):
    """Weighted means and joint covariance of two multiband pixel matrices."""
    wsum = float(np.sum(w)) + 1e-12
    mx = (x * w[:, None]).sum(axis=0) / wsum
    my = (y * w[:, None]).sum(axis=0) / wsum
    xc = x - mx
    yc = y - my
    sxx = (xc * w[:, None]).T @ xc / wsum
    syy = (yc * w[:, None]).T @ yc / wsum
    sxy = (xc * w[:, None]).T @ yc / wsum
    return mx, my, sxx, syy, sxy, xc, yc


def _chi2_cdf(x: np.ndarray, k: int) -> np.ndarray:
    """Chi-square CDF via the regularized lower incomplete gamma function."""
    try:
        from scipy.special import gammainc
        return gammainc(k / 2.0, x / 2.0)
    except Exception:
        # Wilson–Hilferty normal approximation (good for k>=3)
        z = ((x / k) ** (1.0 / 3.0) - (1 - 2.0 / (9 * k))) / np.sqrt(2.0 / (9 * k))
        return 0.5 * (1.0 + np.tanh(z * 0.79788456 * (1 + 0.044715 * z * z)))


def compute_irmad(
    img1: np.ndarray,
    img2: np.ndarray,
    *,
    max_iters: int = 10,
    tol: float = 1e-3,
    max_side: int = 1024,
    eps: float = 1e-6,
) -> Optional[IRMADResult]:
    """Run IR-MAD on an RGB pair. Returns None on numerical failure."""
    try:
        if img1.shape != img2.shape or img1.ndim != 3:
            return None
        full_h, full_w = img1.shape[:2]

        scale = min(1.0, max_side / max(full_h, full_w))
        if scale < 1.0:
            ah, aw = max(64, int(full_h * scale)), max(64, int(full_w * scale))
            a = cv2.resize(img1, (aw, ah), interpolation=cv2.INTER_AREA)
            b = cv2.resize(img2, (aw, ah), interpolation=cv2.INTER_AREA)
        else:
            ah, aw = full_h, full_w
            a, b = img1, img2

        bands = a.shape[2]
        x = a.reshape(-1, bands).astype(np.float64)
        y = b.reshape(-1, bands).astype(np.float64)
        n = x.shape[0]
        w = np.ones(n, dtype=np.float64)

        rho_prev = None
        converged = False
        it = 0
        mads_std = np.zeros_like(x)

        for it in range(1, max_iters + 1):
            _, _, sxx, syy, sxy, xc, yc = _weighted_stats(x, y, w)
            sxx += eps * np.eye(bands)
            syy += eps * np.eye(bands)

            # CCA via generalized eigenproblem on sxx^-1 sxy syy^-1 syx
            isxx = np.linalg.inv(sxx)
            isyy = np.linalg.inv(syy)
            m1 = isxx @ sxy @ isyy @ sxy.T
            evals, evecs = np.linalg.eig(m1)
            order = np.argsort(evals.real)[::-1]
            rho2 = np.clip(evals.real[order], 0.0, 1.0)
            avecs = evecs.real[:, order]

            # Normalize canonical vectors: var(a^T x) = 1
            for j in range(bands):
                va = avecs[:, j] @ sxx @ avecs[:, j]
                avecs[:, j] /= np.sqrt(max(va, eps))
            bvecs = isyy @ sxy.T @ avecs
            for j in range(bands):
                vb = bvecs[:, j] @ syy @ bvecs[:, j]
                bvecs[:, j] /= np.sqrt(max(vb, eps))

            u = xc @ avecs
            v = yc @ bvecs
            # Sign alignment: positive correlation between pairs
            for j in range(bands):
                if np.sum(u[:, j] * v[:, j] * w) < 0:
                    bvecs[:, j] = -bvecs[:, j]
                    v[:, j] = -v[:, j]

            mads = u - v
            rho = np.sqrt(rho2)
            sigma2 = np.maximum(2.0 * (1.0 - rho), eps)  # MAD variances
            mads_std = mads / np.sqrt(sigma2)[None, :]

            chi2 = np.sum(mads_std ** 2, axis=1)
            # No-change probability = 1 - CDF (large chi2 => change)
            w_new = 1.0 - _chi2_cdf(chi2, bands)
            w_new = np.clip(w_new, 1e-6, 1.0)

            if rho_prev is not None and np.max(np.abs(rho - rho_prev)) < tol:
                w = w_new
                converged = True
                break
            rho_prev = rho
            w = w_new

        chi2_map = np.sum(mads_std ** 2, axis=1).reshape(ah, aw)
        # Robust quantile normalization instead of the chi-square CDF: the CDF
        # saturates at ~1.0 for most real pixels, which destroys the graded
        # response needed by percentile-threshold fusion downstream.
        hi = float(np.quantile(chi2_map, 0.995))
        if hi <= 1e-8:
            hi = float(chi2_map.max() + 1e-8)
        prob = np.clip(chi2_map / hi, 0.0, 1.0)
        nc_w = w.reshape(ah, aw)

        prob_full = prob.astype(np.float32)
        ncw_full = nc_w.astype(np.float32)
        if (ah, aw) != (full_h, full_w):
            prob_full = cv2.resize(prob_full, (full_w, full_h), interpolation=cv2.INTER_LINEAR)
            ncw_full = cv2.resize(ncw_full, (full_w, full_h), interpolation=cv2.INTER_LINEAR)

        return IRMADResult(
            chi2=chi2_map.astype(np.float32),
            change_prob=np.clip(prob_full, 0.0, 1.0),
            no_change_weights=np.clip(ncw_full, 0.0, 1.0),
            iterations=it,
            converged=converged,
        )
    except Exception as exc:
        _log.warning("IR-MAD failed (%s)", exc)
        return None


def radiometric_regression_normalize(
    img1: np.ndarray,
    img2: np.ndarray,
    no_change_weights: np.ndarray,
    min_weight: float = 0.5,
) -> Optional[np.ndarray]:
    """Normalize img2 to img1 via weighted linear regression on no-change pixels.

    Classic IR-MAD application: fit per-band ``img1 ~ a*img2 + b`` using only
    pixels the IR-MAD iteration deemed unchanged, then map img2 through the
    fit. More faithful than global mean/std matching because changed pixels
    no longer skew the statistics. Returns uint8 image or None.
    """
    try:
        if img1.shape != img2.shape:
            return None
        w = no_change_weights.astype(np.float64).ravel()
        sel = w >= min_weight
        if np.count_nonzero(sel) < 500:
            return None
        out = img2.astype(np.float64).copy()
        for ch in range(img1.shape[2]):
            s = img2[:, :, ch].astype(np.float64).ravel()[sel]
            t = img1[:, :, ch].astype(np.float64).ravel()[sel]
            ww = w[sel]
            wsum = ww.sum() + 1e-12
            ms, mt = (s * ww).sum() / wsum, (t * ww).sum() / wsum
            cov = ((s - ms) * (t - mt) * ww).sum() / wsum
            var = ((s - ms) ** 2 * ww).sum() / wsum
            if var < 1e-6:
                continue
            gain = cov / var
            if not (0.2 <= gain <= 5.0):
                continue
            offset = mt - gain * ms
            out[:, :, ch] = img2[:, :, ch].astype(np.float64) * gain + offset
        return np.clip(out, 0, 255).astype(np.uint8)
    except Exception as exc:
        _log.warning("IR-MAD regression normalization failed (%s)", exc)
        return None
