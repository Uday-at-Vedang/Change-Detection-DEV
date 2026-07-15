"""
Satellite Change Detection Engine v4
High-accuracy detection with multi-channel analysis, SSIM, CVA, texture features,
adaptive thresholding, vegetation/shadow suppression, SNR-weighted fusion,
SIFT+FLANN registration, tile-based + multi-scale processing, Excess Green
vegetation index, confidence maps, and improved object classification.
"""
import logging
from pathlib import Path

import numpy as np
import cv2
from PIL import Image
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from collections import Counter

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Pre-processing
# ---------------------------------------------------------------------------

def get_detection_max_size() -> int:
    """Max pixel dimension for detection (override with DETECTION_MAX_SIDE env)."""
    from .detection_config import get_detection_max_side
    return get_detection_max_side()


def _ensure_rgb_uint8(img_array):
    """Convert any image array to 3-channel RGB uint8."""
    if img_array.ndim == 2:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
    elif img_array.ndim == 3 and img_array.shape[2] == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
    elif img_array.ndim != 3 or img_array.shape[2] != 3:
        raise ValueError(f"Unsupported image shape: {img_array.shape}")
    if img_array.dtype != np.uint8:
        img_array = np.clip(img_array, 0, 255).astype(np.uint8)
    return img_array


def _to_float32(img):
    """Normalize uint8 image to float32 [0,1]."""
    return img.astype(np.float32) / 255.0


def preprocess_image(image, max_size=None, skip_blur=False):
    """Preprocess image: convert to RGB, limit size, light denoise.

    ``skip_blur`` disables the Gaussian/bilateral denoise so fine change detail
    is preserved — used by full-resolution tiled inference where the whole point
    is to keep native sharpness.
    """
    if max_size is None:
        max_size = get_detection_max_size()
    img_array = np.array(image)
    img_array = _ensure_rgb_uint8(img_array)

    height, width = img_array.shape[:2]
    if max(height, width) > max_size:
        scale = max_size / max(height, width)
        new_w, new_h = max(1, int(width * scale)), max(1, int(height * scale))
        img_array = cv2.resize(img_array, (new_w, new_h), interpolation=cv2.INTER_AREA)

    if skip_blur:
        return img_array

    # Light denoise — smaller kernel preserves fine change detail at high resolution
    blur_ksize = 3 if max_size >= 3000 else 5
    img_array = cv2.GaussianBlur(img_array, (blur_ksize, blur_ksize), 0)
    gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap_var < 60.0:
        img_array = cv2.bilateralFilter(img_array, 5, 50, 50)
    return img_array


# ---------------------------------------------------------------------------
# 2. Improved image registration (alignment)
# ---------------------------------------------------------------------------

def _match_features_sift(gray1, gray2):
    """SIFT + FLANN matching with Lowe's ratio test. Returns (homography, inlier_ratio) or (None, 0)."""
    try:
        sift = cv2.SIFT_create(nfeatures=4000)
        kp1, des1 = sift.detectAndCompute(gray1, None)
        kp2, des2 = sift.detectAndCompute(gray2, None)

        if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
            return None, 0.0

        FLANN_INDEX_KDTREE = 1
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=100)
        flann = cv2.FlannBasedMatcher(index_params, search_params)
        raw_matches = flann.knnMatch(des1, des2, k=2)

        good = [m for m, n in raw_matches if len([m, n]) == 2 and m.distance < 0.7 * n.distance]
        if len(good) < 8:
            return None, 0.0

        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(dst, src, cv2.RANSAC, 4.0, maxIters=3000)
        if H is None or mask is None:
            return None, 0.0
        det = np.linalg.det(H[:2, :2])
        if abs(det) < 0.1 or abs(det) > 10.0:
            return None, 0.0
        return H, float(np.sum(mask)) / len(mask)
    except Exception:
        return None, 0.0


def _match_features_orb(gray1, gray2, max_features=3000):
    """ORB fallback matching. Returns (homography, inlier_ratio) or (None, 0)."""
    best_H, best_ir = None, 0.0
    for nf, ratio_thr in [(max_features, 0.75), (max_features * 2, 0.80)]:
        orb = cv2.ORB_create(nfeatures=nf, scoreType=cv2.ORB_HARRIS_SCORE,
                             edgeThreshold=15, patchSize=31)
        kp1, des1 = orb.detectAndCompute(gray1, None)
        kp2, des2 = orb.detectAndCompute(gray2, None)
        if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
            continue
        bf = cv2.BFMatcher(cv2.NORM_HAMMING)
        raw_matches = bf.knnMatch(des1, des2, k=2)
        good = [m for m, n in raw_matches if len([m, n]) == 2 and m.distance < ratio_thr * n.distance]
        if len(good) < 8:
            continue
        src = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        H, mask = cv2.findHomography(dst, src, cv2.RANSAC, 4.0, maxIters=2000)
        if H is None or mask is None:
            continue
        det = np.linalg.det(H[:2, :2])
        if abs(det) < 0.1 or abs(det) > 10.0:
            continue
        ir = float(np.sum(mask)) / len(mask)
        if ir > best_ir:
            best_H, best_ir = H, ir
    return best_H, best_ir


def _alignment_ncc(img1, img2):
    """Global normalized cross-correlation between two RGB images."""
    g1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY).astype(np.float32).ravel()
    g2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY).astype(np.float32).ravel()
    if g1.size != g2.size or g1.size < 64:
        return 0.0
    c = np.corrcoef(g1, g2)[0, 1]
    return float(c) if np.isfinite(c) else 0.0


def _phase_correlation_translate(img2, gray1, gray2):
    """Shift img2 by phase-correlation offset (same-scale screenshot pairs)."""
    try:
        shift, _ = cv2.phaseCorrelate(gray1.astype(np.float32), gray2.astype(np.float32))
        dx, dy = float(shift[0]), float(shift[1])
        if abs(dx) < 0.5 and abs(dy) < 0.5:
            return img2
        h, w = img2.shape[:2]
        M = np.float32([[1, 0, dx], [0, 1, dy]])
        return cv2.warpAffine(img2, M, (w, h), borderMode=cv2.BORDER_REFLECT)
    except Exception:
        return img2


def register_images(img1, img2, max_features=3000):
    """
    Multi-stage alignment with quality metrics.
    Returns (img1, img2_aligned, registration_ok, reg_meta).
    """
    h, w = img1.shape[:2]
    reg_meta = {
        "method": "none",
        "inlier_ratio": 0.0,
        "ncc": 0.0,
        "homography_used": False,
    }

    if img1.shape[:2] != img2.shape[:2]:
        img2 = cv2.resize(img2, (w, h), interpolation=cv2.INTER_LINEAR)

    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)

    img2 = _phase_correlation_translate(img2, gray1, gray2)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)

    match_method = "sift"
    H, ir = _match_features_sift(gray1, gray2)
    if H is None or ir < 0.25:
        H_orb, ir_orb = _match_features_orb(gray1, gray2, max_features)
        if ir_orb > ir:
            H, ir = H_orb, ir_orb
            match_method = "orb"

    reg_meta["inlier_ratio"] = float(ir)

    if H is not None and ir >= 0.35:
        img2_warped = cv2.warpPerspective(img2, H, (w, h), borderMode=cv2.BORDER_REFLECT)
        img2_refined = _refine_ecc(img1, img2_warped)
        ncc = _alignment_ncc(img1, img2_refined)
        reg_meta.update({
            "method": match_method,
            "ncc": ncc,
            "homography_used": True,
        })
        if ncc >= 0.55:
            return img1, img2_refined, True, reg_meta
        reg_meta["method"] = f"{match_method}_rejected"
        return img1, img2, False, reg_meta

    img1_ecc, img2_ecc, ok, ecc_meta = _register_images_ecc_multiscale(img1, img2)
    reg_meta.update(ecc_meta)
    return img1_ecc, img2_ecc, ok, reg_meta


def _refine_ecc(img1, img2_initial):
    """Refine an already-coarse-aligned image with ECC translation/affine."""
    try:
        gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        gray2 = cv2.cvtColor(img2_initial, cv2.COLOR_RGB2GRAY).astype(np.float32) / 255.0
        h, w = img1.shape[:2]

        warp = np.eye(2, 3, dtype=np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5)

        # Try affine first, fall back to translation
        for motion in [cv2.MOTION_AFFINE, cv2.MOTION_TRANSLATION]:
            try:
                warp_m = np.eye(2, 3, dtype=np.float32)
                cc, warp_m = cv2.findTransformECC(
                    gray1, gray2, warp_m, motion, criteria)
                if cc >= 0.6:
                    aligned = cv2.warpAffine(
                        img2_initial, warp_m, (w, h),
                        flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
                        borderMode=cv2.BORDER_REFLECT)
                    return aligned
            except Exception:
                continue
    except Exception:
        pass
    return img2_initial


def _register_images_ecc_multiscale(img1, img2):
    """
    Multi-scale ECC fallback: start from a downscaled version (faster, wider
    convergence basin), then refine at full resolution.
    """
    try:
        gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)
        h, w = img1.shape[:2]

        # Build 2-level pyramid
        scales = [4, 2, 1]
        warp = np.eye(2, 3, dtype=np.float32)

        for scale in scales:
            sh, sw = h // scale, w // scale
            if sh < 64 or sw < 64:
                continue
            g1 = cv2.resize(gray1, (sw, sh)).astype(np.float32) / 255.0
            g2 = cv2.resize(gray2, (sw, sh)).astype(np.float32) / 255.0

            scaled_warp = warp.copy()
            scaled_warp[0, 2] /= (scales[0] / scale) if scale != scales[0] else 1
            scaled_warp[1, 2] /= (scales[0] / scale) if scale != scales[0] else 1

            iters = 300 if scale == scales[0] else 150
            criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, iters, 1e-6)
            try:
                cc, scaled_warp = cv2.findTransformECC(
                    g1, g2, scaled_warp, cv2.MOTION_AFFINE, criteria)
            except Exception:
                continue

            # Scale translation back for next level
            if scale != 1:
                warp = scaled_warp.copy()
                next_idx = scales.index(scale) + 1
                if next_idx < len(scales):
                    next_scale = scales[next_idx]
                    ratio = scale / next_scale
                    warp[0, 2] *= ratio
                    warp[1, 2] *= ratio
            else:
                warp = scaled_warp

        aligned = cv2.warpAffine(
            img2, warp, (w, h),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT)

        # Check alignment quality via normalized cross-correlation
        g_aligned = cv2.cvtColor(aligned, cv2.COLOR_RGB2GRAY).astype(np.float32)
        g_ref = gray1.astype(np.float32)
        ncc = float(np.corrcoef(g_ref.ravel(), g_aligned.ravel())[0, 1])
        if not np.isfinite(ncc):
            ncc = 0.0
        meta = {"method": "ecc_multiscale", "inlier_ratio": 0.0, "ncc": ncc, "homography_used": False}
        return img1, aligned, bool(ncc >= 0.50), meta
    except Exception:
        return img1, img2, False, {
            "method": "ecc_failed", "inlier_ratio": 0.0, "ncc": 0.0, "homography_used": False,
        }


# ---------------------------------------------------------------------------
# 3. Improved radiometric normalization
# ---------------------------------------------------------------------------

def _match_histogram(src, ref):
    """Map ``src`` intensities so its histogram matches ``ref`` (single channel)."""
    src_u = np.clip(src, 0, 255).astype(np.uint8)
    ref_u = np.clip(ref, 0, 255).astype(np.uint8)
    src_hist = np.bincount(src_u.ravel(), minlength=256).astype(np.float64)
    ref_hist = np.bincount(ref_u.ravel(), minlength=256).astype(np.float64)
    src_cdf = np.cumsum(src_hist) / max(src_u.size, 1)
    ref_cdf = np.cumsum(ref_hist) / max(ref_u.size, 1)
    lut = np.interp(src_cdf, ref_cdf, np.arange(256)).astype(np.float32)
    return lut[src_u]


def normalize_radiometry(img1, img2):
    """Match after image radiometry to before; symmetric CLAHE on L channel.

    CLAHE and histogram matching are env-toggleable (``DETECTION_CLAHE``,
    ``DETECTION_HIST_MATCH``) so each technique can be A/B benchmarked before
    being promoted to a default.
    """
    from .detection_config import get_enable_clahe, get_hist_match

    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    result = lab2.copy()
    for ch in range(3):
        mean1, std1 = np.mean(lab1[:, :, ch]), np.std(lab1[:, :, ch])
        mean2, std2 = np.mean(lab2[:, :, ch]), np.std(lab2[:, :, ch])
        if std2 > 1e-6:
            result[:, :, ch] = (lab2[:, :, ch] - mean2) * (std1 / std2) + mean1

    if get_hist_match():
        # Stronger than mean/std matching: align the full L-channel distribution
        result[:, :, 0] = _match_histogram(result[:, :, 0], lab1[:, :, 0])

    lab1_u = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB)
    lab2_u = np.clip(result, 0, 255).astype(np.uint8)

    if get_enable_clahe():
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        lab1_u[:, :, 0] = clahe.apply(lab1_u[:, :, 0])
        lab2_u[:, :, 0] = clahe.apply(lab2_u[:, :, 0])

    return cv2.cvtColor(lab1_u, cv2.COLOR_LAB2RGB), cv2.cvtColor(lab2_u, cv2.COLOR_LAB2RGB)


# ---------------------------------------------------------------------------
# 4. Vegetation suppression
# ---------------------------------------------------------------------------

def compute_excess_green(img):
    """
    Excess Green Index: ExG = 2G - R - B (normalized to [0,1]).
    Excellent for separating vegetation from soil/buildings in satellite imagery.
    """
    r = img[:, :, 0].astype(np.float32)
    g = img[:, :, 1].astype(np.float32)
    b = img[:, :, 2].astype(np.float32)
    total = r + g + b + 1e-6
    rn, gn, bn = r / total, g / total, b / total
    exg = 2.0 * gn - rn - bn
    return np.clip(exg, 0, 1).astype(np.float32)


def compute_vegetation_mask(img):
    """
    Identify vegetation pixels using three complementary indices:
    1. Pseudo-NDVI (G-R)/(G+R)
    2. Excess Green Index: ExG = 2G - R - B
    3. HSV hue/saturation ranges
    Returns a float map in [0, 1] where 1.0 = vegetation, 0.0 = non-vegetation.
    """
    r = img[:, :, 0].astype(np.float32)
    g = img[:, :, 1].astype(np.float32)
    ndvi = (g - r) / (g + r + 1e-6)

    exg = compute_excess_green(img)

    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0].astype(np.float32)
    sat = hsv[:, :, 1].astype(np.float32)

    ndvi_veg = (ndvi > 0.08).astype(np.float32)
    exg_veg = (exg > 0.05).astype(np.float32)
    hsv_veg = ((hue >= 35) & (hue <= 85) & (sat > 30)).astype(np.float32)

    veg = np.clip(ndvi_veg * 0.4 + exg_veg * 0.3 + hsv_veg * 0.3, 0, 1)
    veg = cv2.GaussianBlur(veg, (11, 11), 0)
    return veg


def compute_combined_vegetation_suppression(img1, img2):
    """
    Asymmetric vegetation handling:
    - Where BOTH images are vegetated: suppress (likely seasonal noise)
    - Where only ONE image is vegetated: boost (real vegetation change)
    Returns a float map where 1.0 = neutral, <1 = suppress, >1 = boost.
    """
    veg1 = compute_vegetation_mask(img1)
    veg2 = compute_vegetation_mask(img2)
    both_veg = np.minimum(veg1, veg2)
    one_only = np.abs(veg1 - veg2)
    seasonal_suppression = 1.0 - both_veg * 0.7
    vegetation_boost = 1.0 + one_only * 0.3
    return (seasonal_suppression * vegetation_boost).astype(np.float32)


# ---------------------------------------------------------------------------
# 5. Shadow / illumination-only change suppression
# ---------------------------------------------------------------------------

def compute_shadow_suppression(img1, img2):
    """
    Detect pixels where only brightness (L) changed but chrominance (A, B)
    stayed similar. These are shadow/illumination shifts, not real changes.
    Returns a float map in [0, 1]: 1.0 = real change, ~0.2 = illumination-only.
    """
    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    delta_l = np.abs(lab1[:, :, 0] - lab2[:, :, 0])
    delta_a = np.abs(lab1[:, :, 1] - lab2[:, :, 1])
    delta_b = np.abs(lab1[:, :, 2] - lab2[:, :, 2])

    chroma_change = delta_a + delta_b
    brightness_only = (delta_l > 18) & (chroma_change < 12)
    shadow_map = brightness_only.astype(np.float32)
    shadow_map = cv2.GaussianBlur(shadow_map, (9, 9), 0)

    suppression = 1.0 - shadow_map * 0.8
    return suppression.astype(np.float32)


# ---------------------------------------------------------------------------
# 6. Change Vector Analysis (CVA)
# ---------------------------------------------------------------------------

def compute_cva(img1, img2):
    """
    Change Vector Analysis in LAB space.
    Returns a normalized change magnitude map with illumination-only
    changes suppressed via direction filtering.
    """
    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    dl = (lab2[:, :, 0] - lab1[:, :, 0]) / 100.0
    da = (lab2[:, :, 1] - lab1[:, :, 1]) / 128.0
    db = (lab2[:, :, 2] - lab1[:, :, 2]) / 128.0

    magnitude = np.sqrt(dl ** 2 + da ** 2 + db ** 2)

    chroma_mag = np.sqrt(da ** 2 + db ** 2)
    total_mag = magnitude + 1e-8
    chroma_ratio = chroma_mag / total_mag

    # Suppress illumination-only changes (low chroma ratio)
    suppression = np.clip(chroma_ratio * 2.5, 0.15, 1.0)
    magnitude = magnitude * suppression

    p995 = float(np.quantile(magnitude, 0.995))
    if p995 > 1e-8:
        magnitude = np.clip(magnitude / p995, 0, 1)

    return magnitude.astype(np.float32)


# ---------------------------------------------------------------------------
# 7. SSIM-based structural change map
# ---------------------------------------------------------------------------

def _ssim_at_scale(gray1, gray2, win_size=11):
    """Compute SSIM dissimilarity at a single scale."""
    sigma = win_size / 6.0
    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    mu1 = cv2.GaussianBlur(gray1, (win_size, win_size), sigma)
    mu2 = cv2.GaussianBlur(gray2, (win_size, win_size), sigma)

    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    sigma1_sq = np.maximum(cv2.GaussianBlur(gray1 * gray1, (win_size, win_size), sigma) - mu1_sq, 0)
    sigma2_sq = np.maximum(cv2.GaussianBlur(gray2 * gray2, (win_size, win_size), sigma) - mu2_sq, 0)
    sigma12 = cv2.GaussianBlur(gray1 * gray2, (win_size, win_size), sigma) - mu1_mu2

    denom = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / (denom + 1e-12)
    dssim = np.clip((1.0 - ssim_map) / 2.0, 0, 1)
    return dssim


def compute_ssim_change_map(img1, img2, win_size=11):
    """
    Multi-scale SSIM dissimilarity: averages full-res and half-res scales
    to capture both fine and coarse structural changes.
    """
    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY).astype(np.float64)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY).astype(np.float64)

    dssim_full = _ssim_at_scale(gray1, gray2, win_size)

    h, w = gray1.shape
    g1_half = cv2.resize(gray1, (max(1, w // 2), max(1, h // 2)))
    g2_half = cv2.resize(gray2, (max(1, w // 2), max(1, h // 2)))
    half_win = max(3, win_size // 2) | 1
    dssim_half = _ssim_at_scale(g1_half, g2_half, half_win)
    dssim_half_up = cv2.resize(dssim_half, (w, h))

    dssim = 0.6 * dssim_full + 0.4 * dssim_half_up
    return dssim


# ---------------------------------------------------------------------------
# 8. Texture feature extraction (LBP)
# ---------------------------------------------------------------------------

def compute_lbp(gray, radius=1, n_points=8):
    """Compute simplified Local Binary Pattern texture descriptor.

    Uses reflect-padded shifts (not np.roll) so opposite image borders do not
    wrap into each other and create spurious texture-change along the frame.
    """
    pad = max(1, int(radius))
    padded = cv2.copyMakeBorder(gray, pad, pad, pad, pad, cv2.BORDER_REFLECT)
    lbp = np.zeros_like(gray, dtype=np.float32)
    h, w = gray.shape
    for i in range(n_points):
        angle = 2 * np.pi * i / n_points
        dx = int(round(radius * np.cos(angle)))
        dy = int(round(-radius * np.sin(angle)))
        shifted = padded[pad + dy:pad + dy + h, pad + dx:pad + dx + w]
        lbp += (shifted >= gray).astype(np.float32)
    return lbp / n_points


def _prob_map_stats(score) -> dict:
    """Summary stats of a [0,1] probability/score map for threshold tuning."""
    if score is None or score.size == 0:
        return {}
    s = score.astype(np.float32)
    return {
        "min": round(float(s.min()), 4),
        "max": round(float(s.max()), 4),
        "mean": round(float(s.mean()), 4),
        "std": round(float(s.std()), 4),
        "p50": round(float(np.percentile(s, 50)), 4),
        "p90": round(float(np.percentile(s, 90)), 4),
        "p99": round(float(np.percentile(s, 99)), 4),
    }


def _hysteresis_threshold(score, high_thr, low_thr):
    """Two-level (hysteresis) thresholding on a [0,1] score map.

    Keeps every low-threshold connected component that contains at least one
    high-threshold "seed" pixel, and drops the rest. This recovers complete
    change blobs (less fragmentation) while removing isolated weak speckle that
    a single hard threshold would either cut or admit.
    """
    score = score.astype(np.float32)
    high = (score >= high_thr).astype(np.uint8)
    if int(high.sum()) == 0:
        return (high * 255).astype(np.uint8)
    low = (score >= min(low_thr, high_thr)).astype(np.uint8)
    num, labels = cv2.connectedComponents(low, connectivity=8)
    if num <= 1:
        return (high * 255).astype(np.uint8)
    seed_labels = np.unique(labels[high > 0])
    seed_labels = seed_labels[seed_labels != 0]
    if seed_labels.size == 0:
        return (high * 255).astype(np.uint8)
    keep = np.isin(labels, seed_labels)
    return np.where(keep, 255, 0).astype(np.uint8)


def compute_texture_change(img1, img2):
    """Compute texture difference using LBP."""
    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY).astype(np.float32)
    lbp1 = compute_lbp(gray1)
    lbp2 = compute_lbp(gray2)
    texture_diff = np.abs(lbp1 - lbp2)
    return texture_diff


# ---------------------------------------------------------------------------
# 9. Edge-aware change detection
# ---------------------------------------------------------------------------

def compute_edge_change(img1, img2):
    """Compute edge-based change map using Canny edges."""
    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)

    # Adaptive Canny thresholds based on median intensity
    med1 = np.median(gray1)
    edges1 = cv2.Canny(gray1, int(max(0, 0.67 * med1)), int(min(255, 1.33 * med1)))
    med2 = np.median(gray2)
    edges2 = cv2.Canny(gray2, int(max(0, 0.67 * med2)), int(min(255, 1.33 * med2)))

    # Dilate edges slightly so nearby edges match
    kernel = np.ones((3, 3), np.uint8)
    edges1_d = cv2.dilate(edges1, kernel, iterations=1)
    edges2_d = cv2.dilate(edges2, kernel, iterations=1)

    # New edges = present in one image but not the other
    edge_change = cv2.absdiff(edges1_d, edges2_d).astype(np.float32) / 255.0
    return edge_change


# ---------------------------------------------------------------------------
# 10. Improved detection methods
# ---------------------------------------------------------------------------

def _adaptive_binary_threshold(score_uint8, min_floor=25, sensitivity=0.5):
    """
    Robust thresholding for noisy scenes.
    Uses max(Otsu, noise-floor, fixed floor) where noise-floor is median + 3*MAD.
    """
    otsu_val, _ = cv2.threshold(
        score_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    median = float(np.median(score_uint8))
    mad = float(np.median(np.abs(score_uint8.astype(np.float32) - median)))
    noise_floor = median + 3.0 * mad
    # Higher sensitivity => lower threshold (detect more), lower sensitivity => stricter
    sens = float(np.clip(sensitivity, 0.0, 1.0))
    sens_shift = int((0.5 - sens) * 24)  # approx -12..+12 around baseline
    thr = int(max(min_floor, otsu_val, noise_floor) + sens_shift)
    thr = max(0, min(255, thr))
    _, mask = cv2.threshold(score_uint8, thr, 255, cv2.THRESH_BINARY)
    return mask, thr, float(otsu_val), float(noise_floor)


def image_difference_method(img1, img2, threshold=0.25, blur_size=5, sensitivity=0.5):
    """Improved image difference with multi-channel analysis and adaptive threshold."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    # Multi-channel difference in LAB (perceptually uniform)
    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    lab1_blur = cv2.GaussianBlur(lab1, (blur_size, blur_size), 0)
    lab2_blur = cv2.GaussianBlur(lab2, (blur_size, blur_size), 0)

    # Weighted Delta-E inspired difference
    diff = lab1_blur - lab2_blur
    delta_e = np.sqrt(
        (diff[:, :, 0] / 100.0) ** 2 +
        (diff[:, :, 1] / 128.0) ** 2 +
        (diff[:, :, 2] / 128.0) ** 2
    )
    delta_e = delta_e / delta_e.max() if delta_e.max() > 0 else delta_e

    delta_uint8 = (delta_e * 255).astype(np.uint8)
    change_mask, used_thr, otsu_val, noise_floor = _adaptive_binary_threshold(
        delta_uint8, min_floor=30, sensitivity=sensitivity
    )

    change_mask = _clean_mask(change_mask)
    debug = {
        "method": "Image Difference",
        "threshold_used": int(used_thr),
        "otsu": float(otsu_val),
        "noise_floor": float(noise_floor),
        "sensitivity": float(sensitivity),
    }
    return change_mask, debug


def feature_based_method(img1, img2, num_clusters=4, sensitivity=0.5):
    """Feature-based change detection using multi-space clustering."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)
    hsv1 = cv2.cvtColor(img1, cv2.COLOR_RGB2HSV).astype(np.float32)
    hsv2 = cv2.cvtColor(img2, cv2.COLOR_RGB2HSV).astype(np.float32)

    diff_lab = np.abs(lab1 - lab2)
    diff_hsv = np.abs(hsv1 - hsv2)

    h, w, _ = diff_lab.shape
    features = np.concatenate([diff_lab, diff_hsv[:, :, 1:]], axis=2)

    # Downsample for KMeans (full-res is too slow for >1M pixels)
    MAX_PIXELS = 250_000
    total = h * w
    if total > MAX_PIXELS:
        scale = np.sqrt(MAX_PIXELS / total)
        sh, sw = max(1, int(h * scale)), max(1, int(w * scale))
        features_small = cv2.resize(features, (sw, sh))
    else:
        features_small = features
        sh, sw = h, w

    features_flat = features_small.reshape(-1, features_small.shape[2])
    scaler = StandardScaler()
    features_scaled = scaler.fit_transform(features_flat)

    kmeans = KMeans(n_clusters=num_clusters, random_state=42, n_init=10)
    labels_small = kmeans.fit_predict(features_scaled)

    cluster_means = [
        np.mean(np.linalg.norm(features_flat[labels_small == i], axis=1))
        if np.any(labels_small == i) else 0.0
        for i in range(num_clusters)
    ]
    change_cluster_idx = np.argmax(cluster_means)

    # Map labels back to full resolution by predicting on all pixels
    if total > MAX_PIXELS:
        full_flat = features.reshape(-1, features.shape[2])
        full_scaled = scaler.transform(full_flat)
        labels = kmeans.predict(full_scaled)
    else:
        labels = labels_small

    change_mask = (labels == change_cluster_idx).astype(np.uint8) * 255
    change_mask = change_mask.reshape(h, w)

    change_mask = _clean_mask(change_mask, sensitivity)
    return change_mask


def kpca_method(img1, img2, sensitivity=0.5):
    """
    Unsupervised change detection via Kernel PCA convolutional mapping
    (KPCAMNet-inspired, see app/cd_models/kpca_method.py). No labels needed —
    filters are fit directly on patches sampled from this image pair. CPU-cheap
    relative to the deep model: ~2 min at 2048px vs. AdaptFormer's ~14 min in
    real testing, at the cost of being a coarser, single-channel-intensity
    signal (no color/texture/edge fusion).
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    from .detection_config import (
        get_kpca_patch_size, get_kpca_n_components, get_kpca_gamma,
        get_kpca_n_train_patches, get_kpca_max_side,
    )
    from .cd_models.kpca_method import kpca_change_mask

    magnitude, kpca_debug = kpca_change_mask(
        img1, img2,
        patch_size=get_kpca_patch_size(),
        n_components=get_kpca_n_components(),
        gamma=get_kpca_gamma(),
        n_train_patches=get_kpca_n_train_patches(),
        max_side=get_kpca_max_side(),
    )
    mag_uint8 = (np.clip(magnitude, 0, 1) * 255).astype(np.uint8)
    change_mask, used_thr, otsu_val, noise_floor = _adaptive_binary_threshold(
        mag_uint8, min_floor=25, sensitivity=sensitivity
    )
    change_mask = _clean_mask(change_mask, sensitivity=sensitivity)

    debug = {
        "method": "KPCA (Unsupervised)",
        "threshold_used": int(used_thr),
        "otsu": float(otsu_val),
        "noise_floor": float(noise_floor),
        "sensitivity": float(sensitivity),
        **kpca_debug,
    }
    return change_mask, debug


def _snr_weight(channel):
    """
    Signal-to-noise ratio weight: signal = mean of top 5% values,
    noise = std of bottom 50%. Channels with concentrated high responses
    score higher than uniformly noisy ones.
    """
    flat = channel.ravel()
    p95 = float(np.quantile(flat, 0.95))
    signal = float(np.mean(flat[flat >= p95])) if p95 > 1e-8 else 0.0
    p50 = float(np.quantile(flat, 0.50))
    noise = float(np.std(flat[flat <= p50])) + 1e-8
    return signal / noise


def _compute_classical_score_map(img1, img2, registration_ok=True):
    """SNR-weighted classical change score in [0,1] before binary threshold."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    color_maps = []
    for scale in (1, 2, 4):
        if scale > 1:
            s1 = cv2.resize(lab1, (lab1.shape[1] // scale, lab1.shape[0] // scale))
            s2 = cv2.resize(lab2, (lab2.shape[1] // scale, lab2.shape[0] // scale))
        else:
            s1, s2 = lab1, lab2
        diff = s1 - s2
        delta_e = np.sqrt((diff[:, :, 0] / 100.0) ** 2 +
                          (diff[:, :, 1] / 128.0) ** 2 +
                          (diff[:, :, 2] / 128.0) ** 2)
        if scale > 1:
            delta_e = cv2.resize(delta_e, (lab1.shape[1], lab1.shape[0]))
        color_maps.append(delta_e)

    color_change = np.mean(color_maps, axis=0)
    color_change = color_change / (color_change.max() + 1e-8)

    ssim_change = compute_ssim_change_map(img1, img2)
    ssim_change = ssim_change / (ssim_change.max() + 1e-8)
    texture_change = compute_texture_change(img1, img2)
    texture_change = texture_change / (texture_change.max() + 1e-8)
    edge_change = compute_edge_change(img1, img2)
    cva_change = compute_cva(img1, img2)

    if not registration_ok:
        ssim_change = ssim_change * 0.45
        edge_change = edge_change * 0.45

    channels = [color_change, ssim_change, texture_change, edge_change, cva_change]
    weights = [_snr_weight(ch) for ch in channels]
    total_w = sum(weights) + 1e-8
    weights = [w / total_w for w in weights]

    fused = np.zeros_like(color_change, dtype=np.float64)
    for ch, w in zip(channels, weights):
        fused += w * ch.astype(np.float64)

    veg_suppression = compute_combined_vegetation_suppression(img1, img2)
    shadow_suppression = compute_shadow_suppression(img1, img2)
    fused = fused * veg_suppression.astype(np.float64) * shadow_suppression.astype(np.float64)

    p995 = float(np.quantile(fused, 0.995))
    if p995 <= 1e-8:
        p995 = float(fused.max() + 1e-8)
    fused_norm = np.clip(fused / (p995 + 1e-8), 0.0, 1.0)
    fused_norm = np.power(fused_norm, 0.85)
    return cv2.GaussianBlur(fused_norm.astype(np.float32), (5, 5), 0), weights


def fuse_dl_and_classical(dl_score, classical_score, img1, img2, sensitivity=0.5):
    """
    Confidence-gated fusion (not union): DL drives structure; classical + ExG for vegetation.
    Returns (mask, final_score_map, debug).
    """
    sens = float(np.clip(sensitivity, 0.0, 1.0))
    h, w = classical_score.shape
    if dl_score is None or dl_score.shape != classical_score.shape:
        dl_score = np.zeros((h, w), dtype=np.float32)

    q = float(np.clip(0.96 - (sens - 0.5) * 0.04, 0.92, 0.98))
    T_cl = float(np.quantile(classical_score, q))

    final_score = 0.65 * dl_score.astype(np.float32) + 0.35 * classical_score

    med_dl, med_cl = 0.35, T_cl * 0.7
    both_agree = (dl_score >= med_dl) & (classical_score >= med_cl)
    final_score = np.where(both_agree, np.maximum(dl_score, classical_score), final_score)

    exg1 = compute_excess_green(img1)
    exg2 = compute_excess_green(img2)
    delta_exg = np.abs(exg2 - exg1)
    veg_boost = (delta_exg > 0.04) & (classical_score >= T_cl * 0.8)
    final_score = np.where(veg_boost, np.maximum(final_score, classical_score), final_score)

    fused_thr = 0.45 + (1.0 - sens) * 0.15
    # Hysteresis grow keeps complete blobs while pruning isolated weak responses
    fused_low = max(0.25, fused_thr - 0.12)
    change_mask = _hysteresis_threshold(final_score, fused_thr, fused_low)
    change_mask = _clean_mask(change_mask, sensitivity=sens)

    debug = {
        "fusion": "confidence_gated",
        "T_dl": 0.40 + (1.0 - sens) * 0.25,
        "T_cl_percentile_q": q,
        "T_cl_score": T_cl,
        "fused_threshold": fused_thr,
        "fused_low_threshold": fused_low,
        "dl_changed_px": int(np.sum(dl_score >= med_dl)),
        "classical_changed_px": int(np.sum(classical_score >= T_cl)),
        "fused_changed_px": int(np.sum(change_mask > 127)),
    }
    return change_mask, final_score, debug


def _ai_fusion_core(img1, img2, sensitivity=0.5, registration_ok=True):
    """Classical-only path: score map + threshold. Returns (mask, score_map, debug)."""
    classical_score, weights = _compute_classical_score_map(
        img1, img2, registration_ok=registration_ok)

    sens = float(np.clip(sensitivity, 0.0, 1.0))
    # Looser percentile than gated fusion — keeps recall for multi-region detection
    q = float(np.clip(0.94 - (sens - 0.5) * 0.06, 0.86, 0.96))
    thr_score = float(np.quantile(classical_score, q))
    # Hysteresis: grow seeds down to a lower percentile to recover full change blobs
    q_low = float(np.clip(q - 0.06, 0.78, q))
    low_score = float(np.quantile(classical_score, q_low))
    change_mask = _hysteresis_threshold(classical_score, thr_score, low_score)
    change_mask = _clean_mask(change_mask, sensitivity=sens)

    debug = {
        "method": "AI-Core",
        "threshold_used": int(thr_score * 255),
        "threshold_percentile_q": q,
        "threshold_score": thr_score,
        "hysteresis_low_q": q_low,
        "hysteresis_low_score": low_score,
        "sensitivity": float(sensitivity),
        "channel_weights": {
            "color": round(weights[0], 4),
            "ssim": round(weights[1], 4),
            "texture": round(weights[2], 4),
            "edge": round(weights[3], 4),
            "cva": round(weights[4], 4),
        },
    }
    return change_mask, classical_score, debug


def _smart_union_fusion(model_mask, rule_mask, dl_score, classical_score, sensitivity=0.5):
    """
    Union with confidence pruning: keep pixels where at least one engine is
    confident, or both agree. Drops weak single-engine speckle (hallucinations).
    """
    from .detection_config import get_dl_floor_base, get_cl_q_base

    sens = float(np.clip(sensitivity, 0.0, 1.0))
    model_on = model_mask > 127
    rule_on = rule_mask > 127
    both_agree = model_on & rule_on

    dl_floor = get_dl_floor_base() + (1.0 - sens) * 0.10
    cl_q = float(np.clip(get_cl_q_base() - (sens - 0.5) * 0.03, 0.50, 0.99))
    cl_floor = (
        float(np.quantile(classical_score, cl_q))
        if float(classical_score.max()) > 1e-6 else 0.38
    )

    dl_ok = dl_score >= dl_floor
    cl_ok = classical_score >= cl_floor
    keep = both_agree | (model_on & dl_ok) | (rule_on & cl_ok)
    return np.where(keep, 255, 0).astype(np.uint8)


def _structural_evidence(diff, feat_a):
    """Score how strongly a region looks like built structure (not water/vegetation)."""
    score = 0.0
    if diff:
        if diff.get("delta_lines", 0) > 2:
            score += 0.28
        if diff.get("delta_corners", 0) > 3:
            score += 0.24
        if diff.get("delta_edge_density", 0) > 8:
            score += 0.20
        if diff.get("hull_ratio_after", 0) > 0.35:
            score += 0.18
        if diff.get("lines_after", 0) > 4:
            score += 0.14
        if diff.get("ssim", 1.0) < 0.65:
            score += 0.12
    if feat_a.get("edge_density", 0) > 35:
        score += 0.18
    if feat_a.get("orientation_entropy", 3.0) < 2.4:
        score += 0.14
    return min(1.0, score)


def _resolve_classification(scores, diff, feat_a):
    """Apply cross-type constraints; fix water vs construction confusion."""
    structural = _structural_evidence(diff, feat_a)

    water = scores.get("Water Body Change", 0.0)
    bld = scores.get("New Construction/Building", 0.0)

    # Water needs smooth, blue, low-edge surface — not just one cue
    water_cues = sum([
        feat_a["blue_ratio"] > 0.38,
        feat_a["edge_density"] < 28,
        feat_a["texture_std"] < 26,
        95 <= feat_a["hue"] <= 130,
        feat_a["lbp_variance"] < 0.045,
    ])
    if water_cues < 3:
        scores["Water Body Change"] = water * 0.45
    elif water_cues < 4:
        scores["Water Body Change"] = water * 0.75

    # Built structure strongly disqualifies water
    if structural >= 0.30:
        scores["Water Body Change"] *= max(0.1, 1.0 - structural * 1.2)
        scores["New Construction/Building"] = min(1.0, bld + structural * 0.45)

    best = max(scores, key=scores.get)
    conf = scores[best]

    # Prefer construction when structural evidence is strong and scores are close
    if (
        best == "Water Body Change"
        and scores["New Construction/Building"] >= conf * 0.72
        and structural >= 0.22
    ):
        best = "New Construction/Building"
        conf = scores["New Construction/Building"]

    return best, conf


def ai_deep_learning_method(img1, img2, sensitivity=0.5, registration_ok=True,
                            dl_score_override=None):
    """
    Dual-engine: AdaptFormer + classical fusion with confidence-pruned union.

    ``dl_score_override`` lets callers supply a precomputed deep score map (e.g.
    full-resolution windowed inference) instead of running AdaptFormer on the
    working-resolution arrays.
    """
    from .model_inference import is_model_available, predict_change_mask

    model_mask = None
    dl_score = None
    model_ok = False
    threshold = 0.32 + (1.0 - float(np.clip(sensitivity, 0, 1))) * 0.22
    tile_stats: dict = {}

    if dl_score_override is not None:
        dl_score = dl_score_override.astype(np.float32)
        if dl_score.shape != img1.shape[:2]:
            dl_score = cv2.resize(dl_score, (img1.shape[1], img1.shape[0]),
                                  interpolation=cv2.INTER_LINEAR)
        model_mask = (dl_score >= threshold).astype(np.uint8) * 255
        model_ok = True
    elif is_model_available():
        try:
            model_mask, dl_score = predict_change_mask(
                img1, img2, threshold=threshold, tile_stats=tile_stats)
            model_ok = dl_score is not None
        except Exception as e:
            _log.warning("AdaptFormer inference failed: %s", e)

    rule_mask, classical_score, core_debug = _ai_fusion_core(
        img1, img2, sensitivity=sensitivity, registration_ok=registration_ok)

    if model_ok and dl_score is not None:
        if model_mask is None:
            model_mask = (dl_score >= threshold).astype(np.uint8) * 255

        from .detection_config import get_fusion_mode
        fusion_mode = get_fusion_mode()
        if fusion_mode == "hysteresis":
            # Confidence-gated hysteresis fusion keeps complete change blobs
            # while pruning isolated weak responses (better recall on big blobs).
            combined, final_score, fuse_dbg = fuse_dl_and_classical(
                dl_score, classical_score, img1, img2, sensitivity=sensitivity)
        else:
            combined = _smart_union_fusion(
                model_mask, rule_mask, dl_score, classical_score, sensitivity=sensitivity)
            combined = _clean_mask(combined, sensitivity=sensitivity)
            final_score = np.maximum(dl_score, classical_score)
            fuse_dbg = {}
        debug = {
            "method": "AI-Based Deep Learning (AdaptFormer + confidence union)",
            "model": "adaptformer-levir-cd",
            "fusion": fusion_mode,
            "threshold_used": int(threshold * 255),
            "sensitivity": float(sensitivity),
            "model_changed_px": int(np.sum(model_mask > 127)),
            "rule_changed_px": int(np.sum(rule_mask > 127)),
            "combined_changed_px": int(np.sum(combined > 127)),
            "fusion_debug": fuse_dbg,
            "dl_score_source": "windowed_fullres" if dl_score_override is not None else "tiled",
            "probabilityMapStats": _prob_map_stats(final_score),
            "tileSkip": tile_stats or None,
            "_score_map": final_score.astype(np.float32),
        }
        return combined, debug

    debug = {
        "method": "AI-Based Deep Learning (classical fallback)",
        "threshold_used": core_debug.get("threshold_used"),
        "sensitivity": float(sensitivity),
        "core": core_debug,
    }
    return rule_mask, debug


def hybrid_method(img1, img2, sensitivity=0.5, registration_ok=True):
    """Hybrid: feature-based + AI fusion (image-difference path removed)."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    feature_mask = feature_based_method(img1, img2, sensitivity=sensitivity)
    ai_mask, ai_debug = ai_deep_learning_method(
        img1, img2, sensitivity=sensitivity, registration_ok=registration_ok)

    combined = (
        0.30 * feature_mask.astype(np.float32) +
        0.70 * ai_mask.astype(np.float32)
    )

    base_thr = 98
    sens = float(np.clip(sensitivity, 0.0, 1.0))
    hybrid_thr = int(np.clip(base_thr + int((0.5 - sens) * 36), 60, 150))
    _, final_mask = cv2.threshold(combined.astype(np.uint8), hybrid_thr, 255, cv2.THRESH_BINARY)
    final_mask = _clean_mask(final_mask, sensitivity=sensitivity)
    debug = {
        "method": "Hybrid Approach",
        "threshold_used": int(hybrid_thr),
        "sensitivity": float(sensitivity),
        "sub_methods": {
            "feature_based": {"method": "Feature-Based"},
            "ai_deep_learning": ai_debug,
        },
    }
    return final_mask, debug


# ---------------------------------------------------------------------------
# 10b. Hybrid AI method (deep learning + classical with confidence map)
# ---------------------------------------------------------------------------

def _build_confidence_map_from_channels(img1, img2, dl_score=None):
    """
    Build a per-pixel confidence map from multiple signal channels.
    Includes color, SSIM, texture, edge, CVA, and optionally a DL score map.
    Returns float32 map in [0,1].
    """
    from .cd_models.model_utils import build_confidence_map

    color = compute_cva(img1, img2)
    ssim = compute_ssim_change_map(img1, img2)
    ssim_norm = ssim / (ssim.max() + 1e-8)
    texture = compute_texture_change(img1, img2)
    texture_norm = texture / (texture.max() + 1e-8)
    edge = compute_edge_change(img1, img2)

    channels = [color, ssim_norm.astype(np.float32), texture_norm.astype(np.float32), edge]
    weights = [0.30, 0.25, 0.15, 0.10]

    if dl_score is not None:
        channels.append(dl_score)
        weights.append(0.40)
        # Re-normalize so weights sum to 1
        total = sum(weights)
        weights = [w / total for w in weights]

    return build_confidence_map(channels, weights)


def _multiscale_classical(img1, img2, sensitivity=0.5, registration_ok=True):
    """Run classical fusion at multiple scales and OR-combine for better recall."""
    from .cd_models.model_utils import multiscale_detect

    def _single_scale_detect(s1, s2):
        mask, _, _ = _ai_fusion_core(
            s1, s2, sensitivity=sensitivity, registration_ok=registration_ok)
        return mask

    return multiscale_detect(_single_scale_detect, img1, img2, scales=(1.0, 0.5))


def hybrid_ai_method(img1, img2, sensitivity=0.5, registration_ok=True):
    """Hybrid AI: DL mask + multi-scale classical mask with confidence weighting."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    from .model_inference import is_model_available, predict_change_mask

    dl_mask = np.zeros(img1.shape[:2], dtype=np.uint8)
    dl_score = np.zeros(img1.shape[:2], dtype=np.float32)
    dl_method = "none"
    thr = 0.25 + (1.0 - float(np.clip(sensitivity, 0, 1))) * 0.25

    if is_model_available():
        try:
            dl_mask, dl_score = predict_change_mask(img1, img2, threshold=thr)
            dl_method = "adaptformer"
        except Exception:
            pass

    if dl_method == "none":
        try:
            from .cd_models.change_model import is_siamese_available, predict_siamese
            if is_siamese_available():
                dl_mask, dl_score = predict_siamese(img1, img2, threshold=thr)
                dl_method = "siamese_unet"
        except Exception:
            pass

    classical_mask = _multiscale_classical(
        img1, img2, sensitivity=sensitivity, registration_ok=registration_ok)
    conf_map = _build_confidence_map_from_channels(
        img1, img2, dl_score=dl_score if dl_method != "none" else None)

    dl_w = 0.7 if dl_method != "none" else 0.0
    cl_w = 1.0 - dl_w
    fused = dl_w * dl_mask.astype(np.float32) + cl_w * classical_mask.astype(np.float32)

    if conf_map is not None:
        conf_boost = np.clip(conf_map * 1.5, 0, 1)
        fused = fused * (0.6 + 0.4 * conf_boost)

    fused_thr = max(88, int(132 - (sensitivity - 0.5) * 55))
    _, final_mask = cv2.threshold(fused.astype(np.uint8), fused_thr, 255, cv2.THRESH_BINARY)
    final_mask = _clean_mask(final_mask, sensitivity=sensitivity)

    debug = {
        "method": f"Hybrid AI ({dl_method} + multi-scale classical)",
        "dl_method": dl_method,
        "threshold_used": fused_thr,
        "sensitivity": float(sensitivity),
        "dl_changed_px": int(np.sum(dl_mask > 127)),
        "classical_changed_px": int(np.sum(classical_mask > 127)),
        "final_changed_px": int(np.sum(final_mask > 127)),
    }
    return final_mask, debug


ALIGNMENT_WARNING_MSG = (
    "Images may differ in zoom/crop; use the same map location, zoom level, and crop "
    "for before and after screenshots."
)


# ---------------------------------------------------------------------------
# 11. Robust post-processing
# ---------------------------------------------------------------------------

def _clean_mask(mask, sensitivity=0.5, border_margin=None):
    """
    Robust morphological cleaning:
    1. Zero-out border pixels (registration artifacts)
    2. Median filter to kill salt-and-pepper noise
    3. Opening to remove small specks
    4. Closing to bridge tiny gaps
    5. Fill holes inside regions
    6. Erode-then-dilate to break thin noise bridges
    7. Connected-component area + circularity filtering

    ``border_margin`` defaults to the env-configured value (smaller in full-res
    mode so genuine edge changes are not discarded).
    """
    if border_margin is None:
        from .detection_config import get_border_margin
        border_margin = get_border_margin()
    mask = mask.copy()
    h, w = mask.shape[:2]

    if border_margin > 0:
        mask[:border_margin, :] = 0
        mask[-border_margin:, :] = 0
        mask[:, :border_margin] = 0
        mask[:, -border_margin:] = 0

    mask = cv2.medianBlur(mask, 3)

    open_size = max(3, int(4 * (1 - sensitivity * 0.5)))
    if open_size % 2 == 0:
        open_size += 1
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)

    close_size = max(3, int(5 * (1 - sensitivity)))
    if close_size % 2 == 0:
        close_size += 1
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)

    k_break = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    filled = cv2.erode(filled, k_break, iterations=1)
    filled = cv2.dilate(filled, k_break, iterations=1)

    # 7. Component-level filtering: remove tiny survivors and elongated noise
    min_component_px = max(120, int(h * w * 0.00005))
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(filled, connectivity=8)
    clean = np.zeros_like(filled)
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_component_px:
            continue
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        bbox_area = max(cw * ch, 1)
        aspect = max(cw, ch) / (min(cw, ch) + 1e-8)
        perimeter_approx = 2 * (cw + ch)
        circularity = (perimeter_approx ** 2) / (bbox_area + 1e-8)
        if circularity > 80 and area < min_component_px * 3:
            continue
        if aspect > 12 and area < min_component_px * 2:
            continue
        clean[labels == i] = 255

    return clean


# ---------------------------------------------------------------------------
# 12. Severity classification and improved visualization
# ---------------------------------------------------------------------------

def _severity_from_region(region, total_pixels):
    """
    Type-aware severity classification.
    Building/structural changes use area + confidence.
    Vegetation changes weight confidence (NDVI delta) more heavily.
    """
    area = region.get("area", 0)
    confidence = region.get("confidence", 0.0)
    obj_type = region.get("object_type", "")
    if total_pixels <= 0:
        return "minor"
    area_ratio = area / total_pixels

    if obj_type in _VEGETATION_TYPES or "Vegetation" in (obj_type or ""):
        score = area_ratio * 600 + confidence * 0.6
        if score < 0.8:
            return "minor"
        if score < 3.0:
            return "moderate"
        return "major"

    if obj_type in _STRUCTURAL_TYPES or obj_type in _BUILDING_TYPES:
        score = area_ratio * 1200 + confidence * 0.4
        if score < 1.2:
            return "minor"
        if score < 4.5:
            return "moderate"
        return "major"

    score = area_ratio * 1000 + confidence * 0.3
    if score < 1.0:
        return "minor"
    if score < 4.0:
        return "moderate"
    return "major"


# RGB colors for severity — high-contrast, colorblind-friendly palette
_SEVERITY_COLORS = {
    "minor": (50, 205, 50),     # Lime green
    "moderate": (255, 165, 0),  # Orange
    "major": (255, 50, 50),     # Bright red
}

# Maximum bounding boxes drawn on the image to avoid visual clutter
_MAX_VISIBLE_BOXES = 30


def visualize_changes(img1, img2, change_mask, regions=None, total_pixels=None):
    """
    Clean visualization: subtle tinted overlay for changed pixels,
    color-coded contour outlines (not filled boxes) for the top regions,
    and compact numbered labels.
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    if change_mask.shape[:2] != img2.shape[:2]:
        change_mask = cv2.resize(change_mask, (img2.shape[1], img2.shape[0]))

    overlay = img2.copy().astype(np.float32)
    mask_bool = change_mask > 127
    mask_float = mask_bool.astype(np.float32)

    # Subtle warm tint on changed pixels (18% alpha) — enough to see, not enough to hide
    tint = np.zeros_like(img2, dtype=np.float32)
    tint[:, :, 0] = 255
    tint[:, :, 1] = 80
    alpha = 0.18
    for c in range(3):
        overlay[:, :, c] = (overlay[:, :, c] * (1 - mask_float * alpha)
                            + tint[:, :, c] * mask_float * alpha)

    overlay_uint8 = np.clip(overlay, 0, 255).astype(np.uint8)
    total_px = total_pixels if total_pixels is not None else (img2.shape[0] * img2.shape[1])

    if regions:
        diag = np.sqrt(img2.shape[0]**2 + img2.shape[1]**2)
        line_thickness = max(1, int(diag / 1100))

        visible = regions[:_MAX_VISIBLE_BOXES]

        for r in visible:
            x, y, w, h = r["bbox"]
            severity = r.get("severity") or _severity_from_region(r, total_px)
            color = _SEVERITY_COLORS.get(severity, (255, 255, 255))

            # Draw only the outline — no fill, keeps the image readable
            cv2.rectangle(overlay_uint8, (x, y), (x + w, y + h), color, line_thickness)

            # Compact label: region number in a small pill
            rid = r.get("id", 0)
            label = str(rid)
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = max(0.32, min(0.48, w / 200))
            txt_thick = 1
            (tw, th), _ = cv2.getTextSize(label, font, font_scale, txt_thick)
            lx = x
            ly = max(th + 3, y - 3)
            # Dark background pill for contrast on any terrain
            cv2.rectangle(overlay_uint8,
                          (lx, ly - th - 2), (lx + tw + 5, ly + 1),
                          (30, 30, 30), cv2.FILLED)
            cv2.putText(overlay_uint8, label, (lx + 2, ly - 1),
                        font, font_scale, color, txt_thick, cv2.LINE_AA)

    return overlay_uint8


# ---------------------------------------------------------------------------
# 13. Improved object classification
# ---------------------------------------------------------------------------

def extract_advanced_features(region):
    """Extract rich features for classification: color, texture, edge, shape."""
    if region.size == 0 or region.shape[0] < 3 or region.shape[1] < 3:
        return None

    hsv = cv2.cvtColor(region, cv2.COLOR_RGB2HSV)
    lab = cv2.cvtColor(region, cv2.COLOR_RGB2LAB)
    gray = cv2.cvtColor(region, cv2.COLOR_RGB2GRAY).astype(np.float32)

    # Color stats
    mean_rgb = np.mean(region, axis=(0, 1))
    std_rgb = np.std(region, axis=(0, 1))
    mean_hsv = np.mean(hsv, axis=(0, 1))
    mean_lab = np.mean(lab, axis=(0, 1))

    total_rgb = np.sum(mean_rgb) + 1e-6
    green_ratio = mean_rgb[1] / total_rgb
    blue_ratio = mean_rgb[2] / total_rgb
    red_ratio = mean_rgb[0] / total_rgb

    # Vegetation indices
    ndvi = (mean_rgb[1] - mean_rgb[0]) / (mean_rgb[1] + mean_rgb[0] + 1e-6)
    exg = float(np.mean(compute_excess_green(region)))

    # Texture
    texture_std = float(np.std(gray))
    lbp = compute_lbp(gray.astype(np.float32))
    lbp_variance = float(np.var(lbp))

    # Edges
    grad_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    grad_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    edge_mag = np.sqrt(grad_x ** 2 + grad_y ** 2)
    edge_density = float(np.mean(edge_mag))

    # Edge orientation histogram (structural regularity)
    angles = np.arctan2(grad_y, grad_x + 1e-8)
    angle_hist, _ = np.histogram(angles, bins=8, range=(-np.pi, np.pi))
    angle_hist = angle_hist / (angle_hist.sum() + 1e-8)
    orientation_entropy = -np.sum(angle_hist[angle_hist > 0] * np.log2(angle_hist[angle_hist > 0] + 1e-10))

    # GLCM-like contrast (simplified: variance of neighbors)
    shifted_r = np.roll(gray, 1, axis=1)
    shifted_d = np.roll(gray, 1, axis=0)
    glcm_contrast = float(np.mean((gray - shifted_r) ** 2 + (gray - shifted_d) ** 2))

    return {
        "mean_rgb": mean_rgb, "std_rgb": std_rgb, "mean_hsv": mean_hsv, "mean_lab": mean_lab,
        "ndvi": ndvi, "exg": exg,
        "texture_std": texture_std, "lbp_variance": lbp_variance,
        "edge_density": edge_density, "orientation_entropy": orientation_entropy,
        "glcm_contrast": glcm_contrast,
        "color_homogeneity": float(np.mean(std_rgb)),
        "brightness": float(mean_lab[0]),
        "green_ratio": green_ratio, "blue_ratio": blue_ratio, "red_ratio": red_ratio,
        "saturation": float(mean_hsv[1]), "hue": float(mean_hsv[0]),
    }


def _permanent_change_evidence(diff, feat_a, area, w, h, fill_ratio=0.5):
    """Score how likely a region is a permanent structural/ground change (not a car)."""
    if diff is None or feat_a is None:
        return 0.0
    score = 0.0
    d_lines = abs(int(diff.get("delta_lines", 0)))
    d_corners = abs(int(diff.get("delta_corners", 0)))
    if d_lines >= 5:
        score += 0.22
    if d_corners >= 7:
        score += 0.22
    if abs(diff.get("delta_edge_density", 0)) >= 12:
        score += 0.18
    # Rectangular blobs (cars included) need new structure, not silhouette alone.
    if diff.get("hull_ratio_after", 0) >= 0.52 and area >= 2500:
        if d_lines >= 4 or d_corners >= 6:
            score += 0.14
    if diff.get("ssim", 1.0) < 0.55 and (d_lines >= 3 or d_corners >= 4):
        score += 0.14
    if area >= 12000 and fill_ratio >= 0.35:
        score += 0.10
    if feat_a.get("orientation_entropy", 3.0) < 2.2 and diff.get("lines_after", 0) >= 8:
        score += 0.10
    return min(1.0, score)


def _is_radiometric_transient(diff, area, w, h, features, img_area=None):
    """
    Detect appeared/disappeared objects (cars, trucks) from color/texture shift
    without meaningful new structure. Robust even when bbox padding adds edges.
    """
    if diff is None or features is None:
        return False
    max_car = _max_vehicle_area(img_area)
    if area < 180 or area > max_car * 2.5:
        return False
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > 9.0:
        return False
    ndvi = float(features.get("ndvi", 0))
    if ndvi > 0.22:
        return False
    lab_d = float(diff.get("lab_color_distance", 0))
    d_lines = abs(int(diff.get("delta_lines", 0)))
    d_corners = abs(int(diff.get("delta_corners", 0)))
    structural = d_lines + d_corners
    ssim = float(diff.get("ssim", 1.0))
    if lab_d < 8 and ssim > 0.72:
        return False
    if area <= max_car and 1.0 <= aspect <= 8.0 and ndvi < 0.20:
        if lab_d >= 10 and structural <= 20:
            return True
        if ssim < 0.65 and structural <= 16:
            return True
    return False


def _max_vehicle_area(img_area=None):
    """Scale-aware upper bound for a single-vehicle footprint in pixels."""
    if not img_area or img_area <= 0:
        return 35000
    return int(max(6000, min(50000, img_area * 0.00028)))


def _is_vehicle_like(area, w, h, features, diff=None, feat_b=None, img_area=None):
    """
    Detect parked/moving cars and similar transient vehicles on aerial imagery.
    These are not permanent ground changes and should be excluded from reports.
    """
    aspect = max(w, h) / max(min(w, h), 1)
    max_car = _max_vehicle_area(img_area)

    if area < 200:
        return True
    if area > max_car * 3:
        return False

    if _is_radiometric_transient(diff, area, w, h, features, img_area=img_area):
        return True

    edge = float(features.get("edge_density", 0))
    ndvi = float(features.get("ndvi", 0))
    entropy = float(features.get("orientation_entropy", 3.0))
    homogeneity = float(features.get("color_homogeneity", 0))
    brightness = float(features.get("brightness", 50))

    lines = int(diff.get("lines_after", 0)) if diff else 0
    corners = int(diff.get("corners_after", 0)) if diff else 0
    hull = float(diff.get("hull_ratio_after", 0.5)) if diff else 0.5
    structural = lines + corners
    perm = _permanent_change_evidence(diff, features, area, w, h)

    # Strong permanent structural change — keep (building, road work, clearing)
    if perm >= 0.42 and area >= 4000:
        return False

    # Typical car / van footprint
    if area <= max_car and 1.05 <= aspect <= 8.0 and ndvi < 0.16:
        if perm < 0.35:
            return True
        if edge > 10 and structural < 20 and hull < 0.72:
            return True
        if homogeneity < 35 and edge > 14 and perm < 0.40:
            return True

    # Appeared / disappeared vehicle: localized radiometric change, no new structure
    if diff and area <= max_car and ndvi < 0.14:
        d_lines = abs(int(diff.get("delta_lines", 0)))
        d_corners = abs(int(diff.get("delta_corners", 0)))
        lab_d = float(diff.get("lab_color_distance", 0))
        if d_lines <= 12 and d_corners <= 16 and perm < 0.34:
            if lab_d > 6 and 1.0 <= aspect <= 7.5:
                return True
        # One timestamp has vehicle-like edges, the other is open pavement
        if feat_b is not None:
            eb = float(feat_b.get("edge_density", 0))
            if abs(edge - eb) > 8 and structural < 18 and perm < 0.36:
                if 1.1 <= aspect <= 6.5 and 25 <= brightness <= 110:
                    return True

    # Elongated vehicle on pavement (often mis-tagged as road change)
    if 1.8 <= aspect <= 7.0 and 300 <= area <= max_car:
        if edge > 14 and structural < 14 and ndvi < 0.12 and perm < 0.32:
            return True

    return False


def _should_suppress_transient_region(area, w, h, feat_a, diff=None, feat_b=None,
                                      img_area=None, fill_ratio=1.0):
    """True when a region should be dropped (vehicles + other non-permanent clutter)."""
    if feat_a is None:
        return area < 500
    if _is_vehicle_like(area, w, h, feat_a, diff=diff, feat_b=feat_b, img_area=img_area):
        return True
    if _is_radiometric_transient(diff, area, w, h, feat_a, img_area=img_area):
        return True
    max_transient = _max_vehicle_area(img_area) * 2
    aspect = max(w, h) / max(min(w, h), 1)
    perm = _permanent_change_evidence(diff, feat_a, area, w, h, fill_ratio)
    if area <= max_transient and perm < 0.26 and aspect < 9.0:
        if float(feat_a.get("ndvi", 0)) < 0.20:
            return True
    return False


def strip_transient_from_mask(change_mask, before_img, after_img):
    """Erase vehicle / fleeting-object components from a binary change mask."""
    if before_img is None or after_img is None or change_mask is None:
        return change_mask
    out = change_mask.copy()
    img_h, img_w = out.shape[:2]
    img_area = img_h * img_w
    binary = (out > 127).astype(np.uint8)
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    removed = 0
    for i in range(1, num_labels):
        raw_area = int(stats[i, cv2.CC_STAT_AREA])
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        w = int(stats[i, cv2.CC_STAT_WIDTH])
        h = int(stats[i, cv2.CC_STAT_HEIGHT])
        # Minimal context — large pads inflate edge/structure scores on car blobs.
        pad = 2
        y1, y2 = max(0, y - pad), min(img_h, y + h + pad)
        x1, x2 = max(0, x - pad), min(img_w, x + w + pad)
        a_crop = after_img[y1:y2, x1:x2]
        b_crop = before_img[y1:y2, x1:x2]
        if a_crop.size == 0:
            continue
        feat_a = extract_advanced_features(a_crop)
        if feat_a is None:
            continue
        feat_b = extract_advanced_features(b_crop) if b_crop.size > 0 else None
        diff = _extract_differential_features(b_crop, a_crop) if b_crop.size > 0 else None
        fill = raw_area / max(w * h, 1)
        if _should_suppress_transient_region(
                raw_area, w, h, feat_a, diff=diff, feat_b=feat_b,
                img_area=img_area, fill_ratio=fill):
            out[labels == i] = 0
            removed += 1
    if removed:
        _log.info("Stripped %d transient/vehicle component(s) from change mask", removed)
    return out


def _is_transient_object(area, w, h, features, diff=None, feat_b=None, img_area=None):
    """
    Filter out transient objects (people, cars, animals, shadows, etc.)
    that are NOT permanent ground/structural changes.
    Returns True if the region is likely transient and should be excluded.
    """
    if _is_vehicle_like(area, w, h, features, diff=diff, feat_b=feat_b, img_area=img_area):
        return True

    aspect_ratio = max(w, h) / max(min(w, h), 1)

    # Very small regions are likely noise, people, or small vehicles
    if area < 550:
        return True

    # Tall narrow regions (aspect > 4) are likely people or poles
    if aspect_ratio > 5.0 and area < 3000:
        return True

    # Very high edge density + small area = likely a person or vehicle
    if features["edge_density"] > 70 and area < 2800:
        return True

    # Extremely high texture variance in small area = likely transient clutter
    if features["texture_std"] > 50 and area < 1800:
        return True

    return False


def _count_line_segments(gray_crop):
    """Count straight line segments using LSD — buildings have many, vegetation has few."""
    if gray_crop.size == 0 or gray_crop.shape[0] < 5 or gray_crop.shape[1] < 5:
        return 0, 0.0
    lsd = cv2.createLineSegmentDetector(0)
    lines, _, _, _ = lsd.detect(gray_crop.astype(np.uint8))
    if lines is None:
        return 0, 0.0
    n_lines = len(lines)
    total_length = sum(
        np.sqrt((l[0][2] - l[0][0])**2 + (l[0][3] - l[0][1])**2)
        for l in lines
    )
    return n_lines, float(total_length)


def _count_corners(gray_crop):
    """Count strong corners — buildings have clustered grid-like corners."""
    if gray_crop.size == 0 or gray_crop.shape[0] < 5 or gray_crop.shape[1] < 5:
        return 0
    corners = cv2.goodFeaturesToTrack(
        gray_crop.astype(np.uint8), maxCorners=100,
        qualityLevel=0.05, minDistance=5)
    return 0 if corners is None else len(corners)


def _rectangular_hull_ratio(gray_crop, threshold=128):
    """Ratio of non-zero area to bounding rect — buildings fill their box."""
    if gray_crop.size == 0:
        return 0.0
    _, binary = cv2.threshold(gray_crop.astype(np.uint8), threshold, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return 0.0
    biggest = max(contours, key=cv2.contourArea)
    contour_area = cv2.contourArea(biggest)
    _, _, rw, rh = cv2.boundingRect(biggest)
    rect_area = max(rw * rh, 1)
    return contour_area / rect_area


def _extract_differential_features(before_crop, after_crop):
    """Extract features from BOTH before and after crops plus their deltas."""
    feat_b = extract_advanced_features(before_crop)
    feat_a = extract_advanced_features(after_crop)
    if feat_b is None or feat_a is None:
        return None

    gray_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2GRAY)
    gray_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2GRAY)

    lines_b, linelen_b = _count_line_segments(gray_b)
    lines_a, linelen_a = _count_line_segments(gray_a)
    corners_b = _count_corners(gray_b)
    corners_a = _count_corners(gray_a)
    hull_b = _rectangular_hull_ratio(gray_b)
    hull_a = _rectangular_hull_ratio(gray_a)

    lab_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_dist = float(np.mean(np.sqrt(np.sum((lab_a - lab_b) ** 2, axis=2))))

    # Fast SSIM approximation using cv2 (avoids scikit-image dependency)
    ssim_val = 1.0
    try:
        if gray_b.shape == gray_a.shape and gray_b.shape[0] >= 7 and gray_b.shape[1] >= 7:
            C1 = (0.01 * 255) ** 2
            C2 = (0.03 * 255) ** 2
            fb = gray_b.astype(np.float64)
            fa = gray_a.astype(np.float64)
            mu_b = cv2.GaussianBlur(fb, (11, 11), 1.5)
            mu_a = cv2.GaussianBlur(fa, (11, 11), 1.5)
            sig_b2 = cv2.GaussianBlur(fb * fb, (11, 11), 1.5) - mu_b * mu_b
            sig_a2 = cv2.GaussianBlur(fa * fa, (11, 11), 1.5) - mu_a * mu_a
            sig_ba = cv2.GaussianBlur(fb * fa, (11, 11), 1.5) - mu_b * mu_a
            numer = (2 * mu_b * mu_a + C1) * (2 * sig_ba + C2)
            denom = (mu_b ** 2 + mu_a ** 2 + C1) * (sig_b2 + sig_a2 + C2)
            ssim_map = numer / (denom + 1e-12)
            ssim_val = float(np.mean(ssim_map))
    except Exception:
        pass

    return {
        "before": feat_b, "after": feat_a,
        "delta_ndvi": feat_a["ndvi"] - feat_b["ndvi"],
        "delta_exg": feat_a["exg"] - feat_b["exg"],
        "delta_green_ratio": feat_a["green_ratio"] - feat_b["green_ratio"],
        "delta_edge_density": feat_a["edge_density"] - feat_b["edge_density"],
        "delta_brightness": feat_a["brightness"] - feat_b["brightness"],
        "delta_texture_std": feat_a["texture_std"] - feat_b["texture_std"],
        "delta_saturation": feat_a["saturation"] - feat_b["saturation"],
        "delta_orientation_entropy": feat_a["orientation_entropy"] - feat_b["orientation_entropy"],
        "delta_lines": lines_a - lines_b,
        "delta_line_length": linelen_a - linelen_b,
        "delta_corners": corners_a - corners_b,
        "lines_after": lines_a, "corners_after": corners_a,
        "lines_before": lines_b, "corners_before": corners_b,
        "hull_ratio_before": hull_b,
        "hull_ratio_after": hull_a,
        "lab_color_distance": lab_dist,
        "ssim": ssim_val,
    }


def classify_object_type(image_region, bbox, before_region=None):
    """
    Classify the type of change in a region.
    When before_region is provided, uses differential (before vs after) analysis
    for dramatically better accuracy. Falls back to single-image analysis otherwise.
    """
    x, y, w, h = bbox
    pad = 5
    y1 = max(0, y - pad)
    y2 = min(image_region.shape[0], y + h + pad)
    x1 = max(0, x - pad)
    x2 = min(image_region.shape[1], x + w + pad)
    after_crop = image_region[y1:y2, x1:x2]

    if after_crop.size == 0 or after_crop.shape[0] < 3 or after_crop.shape[1] < 3:
        return "Unclassified", 0.0

    feat_a = extract_advanced_features(after_crop)
    if feat_a is None:
        return "Unclassified", 0.0

    area = w * h
    img_area = int(image_region.shape[0] * image_region.shape[1])
    diff = None
    feat_b = None
    if before_region is not None:
        by1 = max(0, y - pad)
        by2 = min(before_region.shape[0], y + h + pad)
        bx1 = max(0, x - pad)
        bx2 = min(before_region.shape[1], x + w + pad)
        before_crop = before_region[by1:by2, bx1:bx2]
        if before_crop.size > 0 and before_crop.shape[0] >= 3 and before_crop.shape[1] >= 3:
            diff = _extract_differential_features(before_crop, after_crop)
            feat_b = extract_advanced_features(before_crop)

    if _is_transient_object(area, w, h, feat_a, diff, feat_b=feat_b, img_area=img_area):
        return None, 0.0

    aspect_ratio = max(w, h) / max(min(w, h), 1)
    compactness = (4 * np.pi * area) / ((2 * (w + h)) ** 2 + 1e-6)

    # --- Differential classification when before image is available ---
    scores = {}

    # ---- Water Body Change ----
    water = 0.0
    if diff and _structural_evidence(diff, feat_a) >= 0.25:
        water = 0.0  # structural change — skip water scoring entirely
    else:
        if feat_a["blue_ratio"] > 0.38:
            water += 0.22
        if feat_a["texture_std"] < 26:
            water += 0.18
        if feat_a["edge_density"] < 28:
            water += 0.16
        if 95 <= feat_a["hue"] <= 130:
            water += 0.18
        if feat_a["lbp_variance"] < 0.045:
            water += 0.14
        if feat_a["glcm_contrast"] < 450:
            water += 0.08
        if area > 1200:
            water += 0.04
    scores["Water Body Change"] = water

    # ---- Vegetation Change ----
    veg = 0.0
    if diff:
        # Differential: detect actual vegetation gain or loss
        if abs(diff["delta_ndvi"]) > 0.08:
            veg += 0.25
        # Excess Green Index delta — best single indicator of vegetation change
        if abs(diff.get("delta_exg", 0)) > 0.04:
            veg += 0.20
        elif abs(diff.get("delta_exg", 0)) > 0.02:
            veg += 0.10
        if abs(diff["delta_green_ratio"]) > 0.04:
            veg += 0.15
        if diff["lab_color_distance"] > 15 and (
                diff["before"]["ndvi"] > 0.05 or diff["after"]["ndvi"] > 0.05):
            veg += 0.12
        if abs(diff["delta_saturation"]) > 15 and (
                diff["before"]["green_ratio"] > 0.34 or diff["after"]["green_ratio"] > 0.34):
            veg += 0.12
        if diff["delta_lines"] < 3 and diff["delta_corners"] < 5:
            veg += 0.06
        if area > 500:
            veg += 0.04
    else:
        if feat_a["ndvi"] > 0.05:
            veg += 0.22
        if feat_a["ndvi"] > 0.15:
            veg += 0.10
        if feat_a["green_ratio"] > 0.36:
            veg += 0.18
        if 35 <= feat_a["hue"] <= 85:
            veg += 0.15
        if feat_a["saturation"] > 40:
            veg += 0.10
        if feat_a["orientation_entropy"] > 2.5:
            veg += 0.05
        if area > 500:
            veg += 0.04
    scores["Vegetation Change"] = veg

    # ---- New Construction/Building ----
    bld = 0.0
    if diff:
        # Edge density increase: strong for buildings, lower threshold to catch smaller ones
        ded = diff["delta_edge_density"]
        if ded > 20:
            bld += 0.22
        elif ded > 10:
            bld += 0.16
        elif ded > 5:
            bld += 0.08

        # More ordered structure (lower entropy = more regular geometry)
        doe = diff["delta_orientation_entropy"]
        if doe < -0.6:
            bld += 0.15
        elif doe < -0.2:
            bld += 0.10

        # New straight lines appearing
        dl = diff["delta_lines"]
        if dl > 8:
            bld += 0.16
        elif dl > 3:
            bld += 0.10
        elif dl > 1:
            bld += 0.05

        # New corners appearing
        dc = diff["delta_corners"]
        if dc > 10:
            bld += 0.14
        elif dc > 4:
            bld += 0.10
        elif dc > 1:
            bld += 0.05

        # Vegetation replaced by non-vegetation
        if diff["after"]["ndvi"] < 0.08 and diff["before"]["ndvi"] > 0.02:
            bld += 0.10
        # Brightness increase (concrete/roofing vs bare ground)
        if diff["delta_brightness"] > 8:
            bld += 0.06
        # Rectangular shape in after image (needs structural support — cars are rectangular too)
        if diff["hull_ratio_after"] > 0.50:
            if diff["delta_lines"] > 2 or diff["delta_corners"] > 3:
                bld += 0.10
        elif diff["hull_ratio_after"] > 0.35:
            if diff["delta_lines"] > 1 or diff["delta_corners"] > 2:
                bld += 0.05
        # After image has structural features even if delta is modest
        if diff["lines_after"] > 4 and diff["corners_after"] > 6:
            bld += 0.08
        # LAB color distance (significant visual change)
        if diff["lab_color_distance"] > 25:
            bld += 0.08
        elif diff["lab_color_distance"] > 15:
            bld += 0.04
        # SSIM: low = big structural change; very important for building detection
        ssim = diff.get("ssim", 1.0)
        if ssim < 0.4:
            bld += 0.14
        elif ssim < 0.6:
            bld += 0.10
        elif ssim < 0.75:
            bld += 0.05
        # Low NDVI + high edge density in after = likely built structure
        if diff["after"]["ndvi"] < 0.05 and diff["after"]["edge_density"] > 25:
            bld += 0.08
        # New rectangular shape appearing (hull increased)
        hull_delta = diff["hull_ratio_after"] - diff.get("hull_ratio_before", 0)
        if hull_delta > 0.2:
            bld += 0.06
        if 1.0 <= aspect_ratio <= 5.0:
            bld += 0.06
        if area > 600:
            bld += 0.04
    else:
        if feat_a["orientation_entropy"] < 2.5:
            bld += 0.18
        if feat_a["color_homogeneity"] < 28:
            bld += 0.15
        if 1.0 <= aspect_ratio <= 5.0:
            bld += 0.10
        if 0.2 <= compactness <= 0.95:
            bld += 0.10
        if feat_a["edge_density"] > 25:
            bld += 0.14
        if feat_a["glcm_contrast"] > 300:
            bld += 0.10
        if feat_a["saturation"] < 100:
            bld += 0.08
        if 30 <= feat_a["brightness"] <= 95:
            bld += 0.08
        if area > 600:
            bld += 0.05
    scores["New Construction/Building"] = bld
    if area < _max_vehicle_area(img_area):
        if _is_radiometric_transient(diff, area, w, h, feat_a, img_area=img_area):
            scores["New Construction/Building"] *= 0.15
        elif _permanent_change_evidence(diff, feat_a, area, w, h) < 0.34:
            scores["New Construction/Building"] *= 0.25

    # ---- Demolition/Clearing ----
    demo = 0.0
    if diff:
        ded_neg = diff["delta_edge_density"]
        if ded_neg < -20:
            demo += 0.22
        elif ded_neg < -10:
            demo += 0.16
        elif ded_neg < -5:
            demo += 0.08

        dl_neg = diff["delta_lines"]
        if dl_neg < -8:
            demo += 0.18
        elif dl_neg < -3:
            demo += 0.12
        elif dl_neg < -1:
            demo += 0.05

        dc_neg = diff["delta_corners"]
        if dc_neg < -10:
            demo += 0.15
        elif dc_neg < -4:
            demo += 0.10
        elif dc_neg < -1:
            demo += 0.05

        if diff["delta_texture_std"] > 8:
            demo += 0.10
        if diff["delta_brightness"] > 10:
            demo += 0.10
        # Structural features disappeared
        if diff["lines_before"] > 4 and diff["lines_after"] <= 1:
            demo += 0.10
        # Hull ratio dropped (rectangular structure removed)
        hull_drop = diff.get("hull_ratio_before", 0) - diff["hull_ratio_after"]
        if hull_drop > 0.2:
            demo += 0.08
        # SSIM confirms big structural change
        ssim = diff.get("ssim", 1.0)
        if ssim < 0.5:
            demo += 0.08
        if diff["after"]["ndvi"] > 0.03 and diff["before"]["ndvi"] < 0.02:
            demo += 0.06
        if area > 500:
            demo += 0.04
    else:
        if feat_a["texture_std"] > 30:
            demo += 0.18
        if feat_a["orientation_entropy"] > 2.8:
            demo += 0.15
        if feat_a["color_homogeneity"] > 25:
            demo += 0.15
        if feat_a["brightness"] > 60:
            demo += 0.10
        if feat_a["ndvi"] < 0.05:
            demo += 0.12
        if feat_a["saturation"] < 70:
            demo += 0.10
        if area > 800:
            demo += 0.05
    scores["Demolition/Clearing"] = demo

    # ---- Road/Pavement Change ----
    road = 0.0
    # Car-sized elongated blobs often score as road — skip for sub-building footprints
    if area < _max_vehicle_area(img_area) and aspect_ratio < 6.5:
        scores["Road/Pavement Change"] = 0.0
    else:
        if aspect_ratio > 2.5:
            road += 0.22
        if feat_a["color_homogeneity"] < 22:
            road += 0.18
        if feat_a["texture_std"] < 32:
            road += 0.15
        if feat_a["saturation"] < 65:
            road += 0.12
        if feat_a["orientation_entropy"] < 2.0:
            road += 0.15
        if 35 <= feat_a["brightness"] <= 75:
            road += 0.10
        if compactness < 0.3:
            road += 0.05
        if area > 600:
            road += 0.03
        scores["Road/Pavement Change"] = road

    # ---- Temporary Structure (sheds, tents, makeshift) ----
    tmp = 0.0
    if area < _max_vehicle_area(img_area) and aspect_ratio < 5.0:
        scores["Temporary Structure"] = 0.0
    elif diff:
        ded_t = diff["delta_edge_density"]
        if 3 < ded_t < 20:
            tmp += 0.16
        if diff["delta_lines"] > 0 and diff["delta_lines"] <= 5:
            tmp += 0.12
        if diff["delta_corners"] > 0 and diff["delta_corners"] <= 6:
            tmp += 0.10
        if diff["hull_ratio_after"] < 0.50:
            tmp += 0.10
        if diff["after"]["ndvi"] < 0.08:
            tmp += 0.08
        ssim_t = diff.get("ssim", 1.0)
        if 0.3 < ssim_t < 0.7:
            tmp += 0.10
        if diff["lab_color_distance"] > 10:
            tmp += 0.08
        if 200 <= area <= 5000:
            tmp += 0.08
        if 1.0 <= aspect_ratio <= 3.5:
            tmp += 0.06
    else:
        if feat_a["edge_density"] > 15 and feat_a["edge_density"] < 50:
            tmp += 0.18
        if feat_a["orientation_entropy"] > 2.0:
            tmp += 0.12
        if feat_a["color_homogeneity"] > 20:
            tmp += 0.10
        if feat_a["ndvi"] < 0.08:
            tmp += 0.12
        if 200 <= area <= 5000:
            tmp += 0.10
        if 1.0 <= aspect_ratio <= 3.5:
            tmp += 0.08
        if feat_a["saturation"] < 100:
            tmp += 0.06
    scores["Temporary Structure"] = tmp

    # ---- Bare Land/Soil Change ----
    soil = 0.0
    if feat_a["red_ratio"] > 0.34 and feat_a["green_ratio"] < 0.36:
        soil += 0.20
    if 8 <= feat_a["hue"] <= 38:
        soil += 0.18
    if feat_a["ndvi"] < 0.05:
        soil += 0.18
    if feat_a["texture_std"] < 35:
        soil += 0.12
    if feat_a["lbp_variance"] < 0.04:
        soil += 0.12
    if 40 <= feat_a["saturation"] <= 130:
        soil += 0.10
    if 45 <= feat_a["brightness"] <= 82:
        soil += 0.10
    scores["Bare Land/Soil Change"] = soil

    best, conf = _resolve_classification(scores, diff, feat_a)

    if _should_suppress_transient_region(
            area, w, h, feat_a, diff=diff, feat_b=feat_b, img_area=img_area):
        return None, 0.0

    if conf < 0.28:
        return "Unclassified", conf
    return best, min(conf, 1.0)


def classify_with_ensemble(image_region, bbox, before_region=None):
    """Ensemble: classify full region + sub-regions, vote with confidence weighting."""
    x, y, w, h = bbox
    sub_boxes = [(x, y, w, h)]

    if w > 20 and h > 20:
        hw, hh = w // 2, h // 2
        sub_boxes += [
            (x, y, hw, hh),
            (x + hw, y, hw, hh),
            (x, y + hh, hw, hh),
            (x + hw, y + hh, hw, hh),
            (x + w // 4, y + h // 4, hw, hh),
        ]

    classifications = []
    confidences = []
    transient_count = 0
    for sb in sub_boxes:
        obj_type, conf = classify_object_type(image_region, sb,
                                              before_region=before_region)
        if obj_type is None:
            transient_count += 1
            continue
        if obj_type != "Unclassified":
            classifications.append(obj_type)
            confidences.append(conf)

    if transient_count > len(sub_boxes) // 2:
        return None, 0.0

    if not classifications:
        return classify_object_type(image_region, (x, y, w, h),
                                    before_region=before_region)

    weighted = {}
    counts = Counter(classifications)
    for ot, c in zip(classifications, confidences):
        weighted[ot] = weighted.get(ot, 0) + c

    best_type = max(weighted, key=weighted.get)
    avg_conf = weighted[best_type] / counts[best_type]

    if counts[best_type] / len(classifications) >= 0.6:
        avg_conf = min(1.0, avg_conf * 1.15)

    return best_type, avg_conf


# ---------------------------------------------------------------------------
# 14. Vegetation sub-classification
# ---------------------------------------------------------------------------

_VEGETATION_TYPES = {"Vegetation Change"}


def _compute_region_greenness(crop):
    """Return (ndvi, green_ratio, mean_saturation) for an RGB crop."""
    if crop.size == 0 or crop.shape[0] < 2 or crop.shape[1] < 2:
        return 0.0, 0.0, 0.0
    mean_rgb = np.mean(crop, axis=(0, 1)).astype(np.float64)
    total = np.sum(mean_rgb) + 1e-6
    green_ratio = mean_rgb[1] / total
    ndvi = (mean_rgb[1] - mean_rgb[0]) / (mean_rgb[1] + mean_rgb[0] + 1e-6)
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mean_sat = float(np.mean(hsv[:, :, 1]))
    return float(ndvi), float(green_ratio), mean_sat


def _compute_texture_regularity(gray_crop):
    """Measure how regular/grid-like the texture is (low entropy = regular crops)."""
    if gray_crop.size == 0 or gray_crop.shape[0] < 3 or gray_crop.shape[1] < 3:
        return 3.0
    gx = cv2.Sobel(gray_crop.astype(np.float32), cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_crop.astype(np.float32), cv2.CV_64F, 0, 1, ksize=3)
    angles = np.arctan2(gy, gx + 1e-8)
    hist, _ = np.histogram(angles, bins=8, range=(-np.pi, np.pi))
    hist = hist / (hist.sum() + 1e-8)
    entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0] + 1e-10))
    return float(entropy)


def classify_vegetation_subtype(before_img, after_img, bbox):
    """
    Compare before/after crops to determine vegetation change sub-type.
    Returns (subtype_name, confidence).
    """
    x, y, w, h = bbox
    pad = 5
    y1, y2 = max(0, y - pad), min(before_img.shape[0], y + h + pad)
    x1, x2 = max(0, x - pad), min(before_img.shape[1], x + w + pad)

    before_crop = before_img[y1:y2, x1:x2]
    after_crop = after_img[y1:y2, x1:x2]

    if before_crop.size == 0 or after_crop.size == 0:
        return "Vegetation Change", 0.3

    ndvi_b, green_b, sat_b = _compute_region_greenness(before_crop)
    ndvi_a, green_a, sat_a = _compute_region_greenness(after_crop)

    gray_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2GRAY)
    gray_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2GRAY)
    tex_entropy_b = _compute_texture_regularity(gray_b)
    tex_entropy_a = _compute_texture_regularity(gray_a)

    brightness_b = float(np.mean(gray_b))
    brightness_a = float(np.mean(gray_a))

    ndvi_delta = ndvi_a - ndvi_b
    green_delta = green_a - green_b
    sat_delta = sat_a - sat_b

    scores = {
        "Deforestation/Tree Removal": 0.0,
        "New Vegetation/Growth": 0.0,
        "Crop/Agricultural Change": 0.0,
        "Vegetation Health Decline": 0.0,
        "Seasonal Variation": 0.0,
    }

    # --- Deforestation: was green, now not green ---
    if ndvi_b > 0.08 and ndvi_delta < -0.06:
        scores["Deforestation/Tree Removal"] += 0.30
    if green_b > 0.36 and green_delta < -0.03:
        scores["Deforestation/Tree Removal"] += 0.20
    if brightness_a > brightness_b + 10:
        scores["Deforestation/Tree Removal"] += 0.15
    if sat_delta < -15:
        scores["Deforestation/Tree Removal"] += 0.15
    if tex_entropy_a < tex_entropy_b - 0.3:
        scores["Deforestation/Tree Removal"] += 0.10

    # --- New Vegetation/Growth: was bare, now green ---
    if ndvi_a > 0.08 and ndvi_delta > 0.06:
        scores["New Vegetation/Growth"] += 0.30
    if green_a > 0.36 and green_delta > 0.03:
        scores["New Vegetation/Growth"] += 0.20
    if sat_delta > 15:
        scores["New Vegetation/Growth"] += 0.15
    if brightness_a < brightness_b - 5:
        scores["New Vegetation/Growth"] += 0.10
    if tex_entropy_a > tex_entropy_b + 0.2:
        scores["New Vegetation/Growth"] += 0.10

    # --- Crop/Agricultural Change: regular texture patterns, moderate color shift ---
    is_regular = tex_entropy_b < 2.5 or tex_entropy_a < 2.5
    if is_regular:
        scores["Crop/Agricultural Change"] += 0.25
    if 0.03 < abs(ndvi_delta) < 0.12:
        scores["Crop/Agricultural Change"] += 0.20
    if sat_b > 35 and sat_a > 35:
        scores["Crop/Agricultural Change"] += 0.15
    if abs(green_delta) < 0.04 and abs(ndvi_delta) > 0.02:
        scores["Crop/Agricultural Change"] += 0.15
    area = w * h
    if area > 3000:
        scores["Crop/Agricultural Change"] += 0.10

    # --- Vegetation Health Decline: still green but browning ---
    if ndvi_b > 0.05 and ndvi_a > 0.02 and ndvi_delta < -0.03:
        scores["Vegetation Health Decline"] += 0.25
    if green_b > 0.34 and green_a > 0.30 and green_delta < -0.02:
        scores["Vegetation Health Decline"] += 0.20
    if -20 < sat_delta < -3:
        scores["Vegetation Health Decline"] += 0.20
    if abs(brightness_a - brightness_b) < 15:
        scores["Vegetation Health Decline"] += 0.10

    # --- Seasonal Variation: mild shift in color/texture, both sides green ---
    if ndvi_b > 0.04 and ndvi_a > 0.04 and abs(ndvi_delta) < 0.05:
        scores["Seasonal Variation"] += 0.25
    if abs(green_delta) < 0.03:
        scores["Seasonal Variation"] += 0.20
    if abs(sat_delta) < 12:
        scores["Seasonal Variation"] += 0.15
    if abs(brightness_a - brightness_b) < 12:
        scores["Seasonal Variation"] += 0.15

    best = max(scores, key=scores.get)
    conf = scores[best]
    if conf < 0.25:
        return "Vegetation Change", 0.3
    return best, min(conf, 1.0)


# ---------------------------------------------------------------------------
# 15. Structural change sub-classification
# ---------------------------------------------------------------------------

_STRUCTURAL_TYPES = {"New Construction/Building", "Demolition/Clearing",
                     "Road/Pavement Change", "Temporary Structure"}


def _region_has_structure(crop):
    """Heuristic: does this crop contain building-like structure (edges + regularity)?"""
    if crop.size == 0 or crop.shape[0] < 3 or crop.shape[1] < 3:
        return False, 0.0, 0.0
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    edge_density = float(np.mean(np.sqrt(gx**2 + gy**2)))
    angles = np.arctan2(gy, gx + 1e-8)
    hist, _ = np.histogram(angles, bins=8, range=(-np.pi, np.pi))
    hist = hist / (hist.sum() + 1e-8)
    entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0] + 1e-10))
    has_structure = edge_density > 25 and entropy < 2.8
    return has_structure, edge_density, entropy


def classify_structural_subtype(before_img, after_img, bbox, main_type):
    """
    Compare before/after crops to determine structural change sub-type.
    Returns (subtype_name, confidence).
    """
    x, y, w, h = bbox
    pad = 5
    y1, y2 = max(0, y - pad), min(before_img.shape[0], y + h + pad)
    x1, x2 = max(0, x - pad), min(before_img.shape[1], x + w + pad)

    before_crop = before_img[y1:y2, x1:x2]
    after_crop = after_img[y1:y2, x1:x2]

    if before_crop.size == 0 or after_crop.size == 0:
        return main_type, 0.3

    struct_b, edge_b, ent_b = _region_has_structure(before_crop)
    struct_a, edge_a, ent_a = _region_has_structure(after_crop)

    gray_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2GRAY)
    gray_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2GRAY)
    brightness_b = float(np.mean(gray_b))
    brightness_a = float(np.mean(gray_a))
    texture_b = float(np.std(gray_b))
    texture_a = float(np.std(gray_a))

    hsv_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2HSV)
    hsv_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2HSV)
    sat_b = float(np.mean(hsv_b[:, :, 1]))
    sat_a = float(np.mean(hsv_a[:, :, 1]))

    # Check greenness to detect cleared-to-green or green-to-built transitions
    mean_rgb_b = np.mean(before_crop, axis=(0, 1))
    mean_rgb_a = np.mean(after_crop, axis=(0, 1))
    ndvi_b = (mean_rgb_b[1] - mean_rgb_b[0]) / (mean_rgb_b[1] + mean_rgb_b[0] + 1e-6)
    ndvi_a = (mean_rgb_a[1] - mean_rgb_a[0]) / (mean_rgb_a[1] + mean_rgb_a[0] + 1e-6)

    area = w * h

    if main_type == "Road/Pavement Change":
        return _classify_road_subtype(
            struct_b, struct_a, edge_b, edge_a, brightness_b, brightness_a,
            texture_b, texture_a, area, w, h
        )

    scores = {
        "New Building": 0.0,
        "Building Expansion": 0.0,
        "Renovation/Modification": 0.0,
        "Partial Demolition": 0.0,
        "Full Demolition": 0.0,
        "Infrastructure Change": 0.0,
    }

    # --- New Building: before had no structure, after does ---
    if not struct_b and struct_a:
        scores["New Building"] += 0.35
    if edge_a > edge_b + 15:
        scores["New Building"] += 0.15
    if ent_a < ent_b - 0.3:
        scores["New Building"] += 0.10
    if ndvi_b > 0.05 and ndvi_a < 0.03:
        scores["New Building"] += 0.10
    if sat_a < sat_b - 10:
        scores["New Building"] += 0.10

    # --- Building Expansion: both have structure but after has more ---
    if struct_b and struct_a:
        scores["Building Expansion"] += 0.15
    if struct_b and edge_a > edge_b * 1.2:
        scores["Building Expansion"] += 0.20
    if struct_b and texture_a > texture_b + 5:
        scores["Building Expansion"] += 0.15
    if abs(ent_a - ent_b) < 0.5 and edge_a > edge_b:
        scores["Building Expansion"] += 0.15

    # --- Renovation/Modification: both have structure, similar density but different appearance ---
    if struct_b and struct_a:
        scores["Renovation/Modification"] += 0.15
    if abs(edge_a - edge_b) < 12:
        scores["Renovation/Modification"] += 0.15
    if abs(brightness_a - brightness_b) > 8:
        scores["Renovation/Modification"] += 0.20
    if abs(sat_a - sat_b) > 10:
        scores["Renovation/Modification"] += 0.15
    if abs(texture_a - texture_b) < 10:
        scores["Renovation/Modification"] += 0.10

    # --- Partial Demolition: before had structure, after has less ---
    if struct_b and edge_a < edge_b * 0.7:
        scores["Partial Demolition"] += 0.25
    if struct_b and ent_a > ent_b + 0.3:
        scores["Partial Demolition"] += 0.15
    if texture_a > texture_b + 8:
        scores["Partial Demolition"] += 0.15
    if brightness_a > brightness_b + 10:
        scores["Partial Demolition"] += 0.10

    # --- Full Demolition: before had structure, after is bare/empty ---
    if struct_b and not struct_a:
        scores["Full Demolition"] += 0.35
    if edge_b > 30 and edge_a < 20:
        scores["Full Demolition"] += 0.15
    if texture_b > 25 and texture_a < 20:
        scores["Full Demolition"] += 0.15
    if brightness_a > brightness_b + 15:
        scores["Full Demolition"] += 0.10

    # --- Infrastructure Change: elongated shape, high edge regularity ---
    aspect = max(w, h) / max(min(w, h), 1)
    if aspect > 3.0:
        scores["Infrastructure Change"] += 0.25
    if ent_a < 2.0 or ent_b < 2.0:
        scores["Infrastructure Change"] += 0.15
    if area > 2000 and aspect > 2.5:
        scores["Infrastructure Change"] += 0.15

    best = max(scores, key=scores.get)
    conf = scores[best]
    if conf < 0.25:
        return main_type, 0.3
    return best, min(conf, 1.0)


def _classify_road_subtype(struct_b, struct_a, edge_b, edge_a,
                           brightness_b, brightness_a, texture_b, texture_a,
                           area, w, h):
    """Sub-classify road/pavement changes."""
    scores = {
        "New Road/Pavement": 0.0,
        "Road Widening": 0.0,
        "Road Resurfacing": 0.0,
        "Road Deterioration": 0.0,
    }

    if not struct_b and struct_a:
        scores["New Road/Pavement"] += 0.30
    if edge_a > edge_b + 10:
        scores["New Road/Pavement"] += 0.20
    if brightness_a < brightness_b:
        scores["New Road/Pavement"] += 0.15

    if struct_b and struct_a and edge_a > edge_b * 1.15:
        scores["Road Widening"] += 0.30
    if area > 2000:
        scores["Road Widening"] += 0.15

    if struct_b and struct_a and abs(edge_a - edge_b) < 10:
        scores["Road Resurfacing"] += 0.20
    if abs(brightness_a - brightness_b) > 12:
        scores["Road Resurfacing"] += 0.25
    if abs(texture_a - texture_b) < 8:
        scores["Road Resurfacing"] += 0.15

    if texture_a > texture_b + 10:
        scores["Road Deterioration"] += 0.25
    if edge_a < edge_b - 5:
        scores["Road Deterioration"] += 0.20
    if brightness_a > brightness_b + 8:
        scores["Road Deterioration"] += 0.15

    best = max(scores, key=scores.get)
    conf = scores[best]
    if conf < 0.25:
        return "Road/Pavement Change", 0.3
    return best, min(conf, 1.0)


# ---------------------------------------------------------------------------
# 16. 3D Building Analysis — height estimation + construction stage
# ---------------------------------------------------------------------------

_BUILDING_TYPES = {"New Construction/Building", "Demolition/Clearing"}
_STORY_HEIGHT_M = 3.0  # assumed metres per story


def _detect_shadow_region(before_gray, after_gray, bbox, expand=0.6):
    """
    Find new shadow pixels adjacent to a building bbox.
    Returns a binary mask of likely shadow pixels in the expanded bbox area.
    """
    x, y, w, h = bbox
    img_h, img_w = after_gray.shape[:2]

    # Expand bbox to capture shadows cast beside the building
    ex = int(w * expand)
    ey = int(h * expand)
    x1 = max(0, x - ex)
    y1 = max(0, y - ey)
    x2 = min(img_w, x + w + ex)
    y2 = min(img_h, y + h + ey)

    before_crop = before_gray[y1:y2, x1:x2].astype(np.float32)
    after_crop = after_gray[y1:y2, x1:x2].astype(np.float32)

    if before_crop.size == 0 or after_crop.size == 0:
        return None, 0

    # New shadow = pixels that got significantly darker in the after image
    darkening = before_crop - after_crop
    dark_thresh = max(25, np.std(darkening) * 1.5)
    shadow_mask = (darkening > dark_thresh).astype(np.uint8) * 255

    # Remove shadow pixels inside the building footprint itself
    bx1, by1 = x - x1, y - y1
    bx2, by2 = bx1 + w, by1 + h
    bx1, by1 = max(0, bx1), max(0, by1)
    bx2 = min(shadow_mask.shape[1], bx2)
    by2 = min(shadow_mask.shape[0], by2)
    shadow_mask[by1:by2, bx1:bx2] = 0

    # Clean noise
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    shadow_mask = cv2.morphologyEx(shadow_mask, cv2.MORPH_OPEN, kernel)

    shadow_pixels = np.sum(shadow_mask > 0)
    return shadow_mask, shadow_pixels


def estimate_building_height(before_img, after_img, bbox, features):
    """
    Estimate building stories and height from shadow length and footprint geometry.
    Returns (estimated_stories, estimated_height_m).
    """
    before_gray = cv2.cvtColor(before_img, cv2.COLOR_RGB2GRAY)
    after_gray = cv2.cvtColor(after_img, cv2.COLOR_RGB2GRAY)
    x, y, w, h = bbox

    shadow_mask, shadow_px = _detect_shadow_region(before_gray, after_gray, bbox)

    short_side = max(min(w, h), 1)
    footprint_area = w * h

    # --- Shadow-based estimate ---
    shadow_ratio = 0.0
    if shadow_mask is not None and shadow_px > 20:
        # Measure max extent of shadow perpendicular to building edge
        coords = np.column_stack(np.where(shadow_mask > 0))
        if len(coords) > 5:
            # Shadow length = extent along the longer axis of shadow cluster
            spread_y = coords[:, 0].max() - coords[:, 0].min()
            spread_x = coords[:, 1].max() - coords[:, 1].min()
            shadow_length = max(spread_y, spread_x)
            shadow_ratio = shadow_length / short_side

    # --- Footprint-based estimate ---
    aspect = max(w, h) / max(short_side, 1)
    # Compact footprints (aspect < 2.5) tend to be multi-story; elongated are single-story
    footprint_factor = 1.0
    if aspect > 3.0:
        footprint_factor = 0.5  # likely single-story warehouse/industrial
    elif aspect < 1.5 and footprint_area > 2000:
        footprint_factor = 1.3  # compact large footprint = likely taller

    # --- Texture regularity bonus ---
    # Buildings with low orientation entropy (regular structure) tend to be taller
    regularity_bonus = 0.0
    if features and features.get("orientation_entropy", 3.0) < 2.2:
        regularity_bonus = 0.5

    # --- Combine signals ---
    # Base: shadow ratio maps ~0.3-0.5 per story in typical nadir imagery
    if shadow_ratio > 0.1:
        raw_stories = shadow_ratio / 0.35
    else:
        # No clear shadow: use footprint area as rough proxy
        if footprint_area > 5000:
            raw_stories = 3.0
        elif footprint_area > 2000:
            raw_stories = 2.0
        else:
            raw_stories = 1.0

    raw_stories = raw_stories * footprint_factor + regularity_bonus
    stories = max(1, min(50, int(round(raw_stories))))
    height_m = round(stories * _STORY_HEIGHT_M, 1)

    return stories, height_m


def classify_construction_stage(features, bbox):
    """
    Classify construction stage from visual features.
    Returns (stage_name, confidence).
    """
    if features is None:
        return "Unknown", 0.0

    w, h = bbox[2], bbox[3]
    area = w * h

    scores = {
        "Foundation": 0.0,
        "Structural": 0.0,
        "Under Construction": 0.0,
        "Complete": 0.0,
    }

    tex = features.get("texture_std", 30)
    edge = features.get("edge_density", 40)
    orient = features.get("orientation_entropy", 2.5)
    homog = features.get("color_homogeneity", 25)
    bright = features.get("brightness", 60)
    sat = features.get("saturation", 50)
    glcm = features.get("glcm_contrast", 500)
    lbp_var = features.get("lbp_variance", 0.04)

    # --- Foundation ---
    # Flat, low-texture, soil/concrete colored, homogeneous
    if tex < 22:
        scores["Foundation"] += 0.25
    if edge < 30:
        scores["Foundation"] += 0.20
    if homog < 20:
        scores["Foundation"] += 0.20
    if 40 <= bright <= 75:
        scores["Foundation"] += 0.15
    if sat < 60:
        scores["Foundation"] += 0.10
    if lbp_var < 0.03:
        scores["Foundation"] += 0.10

    # --- Structural/Framing ---
    # High edges, geometric regularity, high contrast grid patterns
    if edge > 50:
        scores["Structural"] += 0.25
    if orient < 2.2:
        scores["Structural"] += 0.20
    if glcm > 800:
        scores["Structural"] += 0.20
    if tex > 30:
        scores["Structural"] += 0.15
    if homog > 30:
        scores["Structural"] += 0.10
    if area > 1000:
        scores["Structural"] += 0.10

    # --- Under Construction ---
    # Mixed materials, irregular texture, medium-high edge density
    if 25 < tex < 50:
        scores["Under Construction"] += 0.20
    if 35 < edge < 65:
        scores["Under Construction"] += 0.20
    if orient > 2.6:
        scores["Under Construction"] += 0.20
    if homog > 25:
        scores["Under Construction"] += 0.15
    if 0.03 < lbp_var < 0.07:
        scores["Under Construction"] += 0.15
    if sat < 80:
        scores["Under Construction"] += 0.10

    # --- Complete ---
    # Uniform roof, clean edges, low entropy, consistent color
    if tex < 28:
        scores["Complete"] += 0.20
    if orient < 2.3:
        scores["Complete"] += 0.25
    if homog < 22:
        scores["Complete"] += 0.20
    if edge > 25:
        scores["Complete"] += 0.10
    if lbp_var < 0.04:
        scores["Complete"] += 0.15
    if bright > 50:
        scores["Complete"] += 0.10

    best = max(scores, key=scores.get)
    conf = scores[best]

    if conf < 0.25:
        return "Unknown", conf
    return best, min(conf, 1.0)


def analyze_building_3d(before_img, after_img, region, features):
    """
    Run 3D analysis on a single building/construction region.
    Enriches the region dict with stories, height, and construction stage.
    """
    bbox = region["bbox"]

    stories, height_m = estimate_building_height(before_img, after_img, bbox, features)
    stage, stage_conf = classify_construction_stage(features, bbox)

    region["estimated_stories"] = stories
    region["estimated_height_m"] = height_m
    region["construction_stage"] = stage
    region["construction_stage_confidence"] = stage_conf
    return region


# ---------------------------------------------------------------------------
# 17. Region analysis
# ---------------------------------------------------------------------------

def _tight_bbox(labels, label_id, stats_row):
    """
    Compute a tighter bounding box using actual changed pixels.
    Falls back to the connected-component bbox if the mask is dense enough.
    """
    x = stats_row[cv2.CC_STAT_LEFT]
    y = stats_row[cv2.CC_STAT_TOP]
    w = stats_row[cv2.CC_STAT_WIDTH]
    h = stats_row[cv2.CC_STAT_HEIGHT]
    area = stats_row[cv2.CC_STAT_AREA]

    fill_ratio = area / max(w * h, 1)

    # If the component fills less than 20% of its bbox, compute a tighter fit
    if fill_ratio < 0.20 and area > 100:
        ys, xs = np.where(labels == label_id)
        if len(xs) > 0:
            x = int(np.min(xs))
            y = int(np.min(ys))
            w = int(np.max(xs) - x + 1)
            h = int(np.max(ys) - y + 1)
            fill_ratio = area / max(w * h, 1)

    return x, y, w, h, fill_ratio


def _iou(boxA, boxB):
    """Intersection-over-union for two (x,y,w,h) boxes."""
    ax1, ay1, aw, ah = boxA
    bx1, by1, bw, bh = boxB
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh

    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    union = aw * ah + bw * bh - inter
    return inter / max(union, 1)


def _nms_regions(regions, iou_thresh=0.45):
    """Non-maximum suppression: keep the highest-area box when two overlap."""
    if len(regions) < 2:
        return regions
    keep = []
    used = set()
    for i, r in enumerate(regions):
        if i in used:
            continue
        keep.append(r)
        for j in range(i + 1, len(regions)):
            if j in used:
                continue
            if _iou(r["bbox"], regions[j]["bbox"]) > iou_thresh:
                used.add(j)
    return keep


def analyze_change_regions(change_mask, image, min_area=400, use_ensemble=True,
                           before_img=None, registration_ok=True):
    """
    Find connected change regions with strict quality filters:
    - Adaptive min_area scaled to image size
    - Fill-ratio filter (>= 0.12) rejects sparse noise boxes
    - Tighter bounding boxes computed from actual pixel coordinates
    - NMS to remove overlapping/duplicate boxes
    - Max 60 regions cap to avoid flooding the UI
    """
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(
        change_mask, connectivity=8)
    change_regions = []
    region_id = 0

    img_h, img_w = change_mask.shape[:2]
    img_area = img_h * img_w
    # Adaptive minimum region size:
    # - keeps sensitivity on smaller images
    # - suppresses speckle noise on larger images
    if min_area is None:
        min_area = int(max(250, min(1000, img_area * 0.00009)))

    # Poor registration → residual misalignment shows up as many small false
    # positives. Raise the size floor to keep only confident, larger changes.
    if not registration_ok:
        min_area = int(min_area * 1.4)

    for i in range(1, num_labels):
        raw_area = stats[i, cv2.CC_STAT_AREA]
        if raw_area < min_area:
            continue

        x, y, w, h, fill_ratio = _tight_bbox(labels, i, stats[i])

        # Reject very sparse regions (bbox is mostly empty)
        if fill_ratio < 0.15:
            continue

        # Keep large real changes; only suppress near-full-frame artifacts.
        # When registration failed, allow larger regions to avoid missing true changes.
        max_region_cover = 0.92 if not registration_ok else 0.75
        if (w * h) > img_area * max_region_cover and fill_ratio < 0.35:
            continue

        cx, cy = centroids[i]

        if use_ensemble and raw_area > 500:
            object_type, confidence = classify_with_ensemble(
                image, (x, y, w, h), before_region=before_img)
        else:
            object_type, confidence = classify_object_type(
                image, (x, y, w, h), before_region=before_img)

        if object_type is None:
            # Keep large coherent regions as generic ground change only when well-filled
            if raw_area >= max(min_area * 2, 900) and fill_ratio >= 0.20:
                object_type = "Unclassified Ground Change"
                confidence = max(0.2, min(0.5, fill_ratio))
            else:
                continue

        # Drop vehicles and other non-permanent clutter (any label)
        pad = 2
        ry1, ry2 = max(0, y - pad), min(image.shape[0], y + h + pad)
        rx1, rx2 = max(0, x - pad), min(image.shape[1], x + w + pad)
        crop = image[ry1:ry2, rx1:rx2]
        feat_chk = extract_advanced_features(crop) if crop.size > 0 else None
        diff_chk = feat_b_chk = None
        if before_img is not None and feat_chk is not None:
            by1, by2 = max(0, y - pad), min(before_img.shape[0], y + h + pad)
            bx1, bx2 = max(0, x - pad), min(before_img.shape[1], x + w + pad)
            b_crop = before_img[by1:by2, bx1:bx2]
            if b_crop.size > 0:
                diff_chk = _extract_differential_features(b_crop, crop)
                feat_b_chk = extract_advanced_features(b_crop)
        if feat_chk and _should_suppress_transient_region(
                raw_area, w, h, feat_chk, diff=diff_chk, feat_b=feat_b_chk,
                img_area=img_area, fill_ratio=fill_ratio):
            continue

        if confidence < 0.24 and raw_area < min_area * 3:
            continue

        region_id += 1
        region = {
            "id": region_id,
            "area": int(raw_area),
            "bbox": (x, y, w, h),
            "center": (int(cx), int(cy)),
            "object_type": object_type,
            "confidence": confidence,
            "fill_ratio": round(fill_ratio, 3),
            "sub_type": None,
            "sub_type_confidence": None,
            "estimated_stories": None,
            "estimated_height_m": None,
            "construction_stage": None,
        }

        if before_img is not None:
            if object_type in _VEGETATION_TYPES:
                sub, sub_conf = classify_vegetation_subtype(
                    before_img, image, (x, y, w, h))
                region["sub_type"] = sub
                region["sub_type_confidence"] = sub_conf

            elif object_type in _STRUCTURAL_TYPES:
                sub, sub_conf = classify_structural_subtype(
                    before_img, image, (x, y, w, h), object_type)
                region["sub_type"] = sub
                region["sub_type_confidence"] = sub_conf

                if object_type in _BUILDING_TYPES:
                    pad = 5
                    ry1 = max(0, y - pad)
                    ry2 = min(image.shape[0], y + h + pad)
                    rx1 = max(0, x - pad)
                    rx2 = min(image.shape[1], x + w + pad)
                    crop = image[ry1:ry2, rx1:rx2]
                    feats = extract_advanced_features(crop) if crop.size > 0 else None
                    analyze_building_3d(before_img, image, region, feats)

        change_regions.append(region)

    # Sort by area descending, apply NMS, cap at 60
    change_regions.sort(key=lambda r: r["area"], reverse=True)
    change_regions = _nms_regions(change_regions, iou_thresh=0.45)
    change_regions = change_regions[:60]

    # Re-number after filtering
    for idx, r in enumerate(change_regions, start=1):
        r["id"] = idx

    total_px = img_area
    for r in change_regions:
        r["severity"] = _severity_from_region(r, total_px)

    return change_regions


# ---------------------------------------------------------------------------
# 18. Main pipeline
# ---------------------------------------------------------------------------

def run_detection(before_pil, after_pil, method="AI-Based Deep Learning",
                  enable_registration=True, enable_normalization=True,
                  detection_sensitivity=0.5, min_region_area=None,
                  max_size=None, on_progress=None,
                  before_path=None, after_path=None):
    """Run full detection pipeline; returns change_mask, result_image, stats, regions."""
    from .detection_config import (
        get_inference_mode, get_load_max_side, get_skip_preblur,
        get_windowed_threshold, get_tile_size, get_tile_overlap,
    )

    def _prog(pct, stage):
        if on_progress:
            on_progress(int(pct), stage)

    inference_mode = get_inference_mode()
    skip_blur = get_skip_preblur()

    # Decide whether to stream the deep score from native GeoTIFF windows.
    # Triggered for very large rasters (native side > threshold) or when loading
    # the full-res array would blow the memory budget, so peak RAM stays bounded
    # while the model still sees full-resolution detail.
    windowed = False
    if inference_mode == "fullres_tiled" and before_path and after_path:
        try:
            from .dda.geotiff_io import read_native_size
            from .detection_config import get_tile_memory_budget_mb
            thr = get_windowed_threshold()
            na = read_native_size(Path(before_path))
            nb = read_native_size(Path(after_path))
            if na and nb and na == nb:
                cap = get_load_max_side()
                long_side = min(max(na), cap)
                # ~3 bytes/px/array, two timestamps held in memory at once
                est_mb = (long_side * long_side * 3 * 2) / (1024 * 1024)
                budget = get_tile_memory_budget_mb()
                over_threshold = bool(thr and max(na) > thr)
                over_budget = bool(budget and est_mb > budget)
                if over_threshold or over_budget:
                    windowed = True
        except Exception as exc:
            _log.warning("Windowed-size probe failed: %s", exc)

    if inference_mode == "fullres_tiled" and not windowed:
        # Keep full imagery at the full-res cap and skip denoise.
        ms = max_size if (max_size and max_size > get_detection_max_size()) else get_load_max_side()
    else:
        # Windowed mode keeps the in-memory arrays bounded (registration +
        # classical run on the preview); detail comes from the streamed score.
        ms = max_size or get_detection_max_size()

    _prog(5, "Preprocessing images")
    before_array = preprocess_image(before_pil, max_size=ms, skip_blur=skip_blur)
    after_array = preprocess_image(after_pil, max_size=ms, skip_blur=skip_blur)

    registration_ok = False
    reg_meta = {}
    if enable_registration:
        _prog(20, "Registering images")
        before_array, after_array, registration_ok, reg_meta = register_images(
            before_array, after_array)
    if enable_normalization:
        _prog(35, "Normalizing radiometry")
        before_array, after_array = normalize_radiometry(before_array, after_array)

    alignment_warning = None
    if enable_registration and not registration_ok:
        alignment_warning = ALIGNMENT_WARNING_MSG

    dl_score_override = None
    if windowed and method in ("AI-Based Deep Learning", "Hybrid AI"):
        try:
            from .model_inference import is_model_available, predict_change_score_windowed
            if is_model_available():
                _prog(45, "Full-res tiled inference")
                out_h, out_w = before_array.shape[:2]

                def _win_prog(frac):
                    _prog(45 + int(frac * 5), "Full-res tiled inference")

                dl_score_override = predict_change_score_windowed(
                    before_path, after_path, out_h, out_w,
                    tile_size=get_tile_size(), overlap=get_tile_overlap(),
                    on_progress=_win_prog,
                )
        except Exception as exc:
            _log.warning("Windowed inference failed, falling back to in-memory: %s", exc)
            dl_score_override = None

    _prog(50, f"Running {method}")

    # Image Difference removed from the product; map legacy requests to AI path.
    if method == "Image Difference":
        method = "AI-Based Deep Learning"
        _log.info("Image Difference is deprecated; using AI-Based Deep Learning")

    if method == "AI-Based Deep Learning":
        change_mask, threshold_debug = ai_deep_learning_method(
            before_array, after_array,
            sensitivity=detection_sensitivity,
            registration_ok=registration_ok,
            dl_score_override=dl_score_override,
        )
    elif method == "Feature-Based":
        change_mask = feature_based_method(
            before_array, after_array, sensitivity=detection_sensitivity)
        threshold_debug = {
            "method": "Feature-Based",
            "threshold_used": None,
            "note": "KMeans clustering path does not use binary threshold.",
            "sensitivity": float(detection_sensitivity),
        }
    elif method == "KPCA (Unsupervised)":
        change_mask, threshold_debug = kpca_method(
            before_array, after_array, sensitivity=detection_sensitivity)
    elif method == "Hybrid AI":
        change_mask, threshold_debug = hybrid_ai_method(
            before_array, after_array,
            sensitivity=detection_sensitivity,
            registration_ok=registration_ok,
        )
    else:
        change_mask, threshold_debug = hybrid_method(
            before_array, after_array,
            sensitivity=detection_sensitivity,
            registration_ok=registration_ok,
        )

    # Pull the (numpy) probability map out of the debug dict so the returned
    # threshold_debug stays JSON-serializable; stash it privately in stats for
    # optional probability-PNG export downstream.
    score_map = None
    if isinstance(threshold_debug, dict):
        score_map = threshold_debug.pop("_score_map", None)
        from .detection_config import (
            get_tile_size as _gts, get_tile_overlap as _gto,
            get_multiscale_scales as _gms, get_fusion_mode as _gfm,
        )
        threshold_debug["inferenceMode"] = inference_mode
        threshold_debug["windowed"] = bool(windowed)
        threshold_debug["fusionMode"] = _gfm()
        threshold_debug["tileConfig"] = {"tileSize": _gts(), "overlap": _gto(),
                                         "multiscale": _gms()}

    total_pixels = int(change_mask.shape[0] * change_mask.shape[1])
    changed_pixels_ratio = (
        float(np.sum(change_mask > 127)) / float(total_pixels) if total_pixels else 0.0
    )

    change_mask = strip_transient_from_mask(change_mask, before_array, after_array)

    _prog(65, "Analyzing change regions")
    change_regions = analyze_change_regions(
        change_mask,
        after_array,
        min_area=min_region_area,
        before_img=before_array,
        registration_ok=registration_ok,
    )

    if (
        method in ("AI-Based Deep Learning", "Hybrid Approach", "Hybrid AI")
        and len(change_regions) == 0
        and registration_ok
        and changed_pixels_ratio == 0.0
    ):
        fb_mask = feature_based_method(
            before_array, after_array, sensitivity=detection_sensitivity)
        change_mask = strip_transient_from_mask(fb_mask, before_array, after_array)
        fb_regions = analyze_change_regions(
            change_mask, after_array, min_area=min_region_area,
            before_img=before_array, registration_ok=registration_ok,
        )
        if len(fb_regions) > 0:
            change_regions = fb_regions
            threshold_debug = {
                "method": f"{method} (fallback->Feature-Based)",
                "fallback_used": True,
                "sensitivity": float(detection_sensitivity),
            }

    total_pixels = int(change_mask.shape[0] * change_mask.shape[1])
    _prog(85, "Building visualization")
    result_image = visualize_changes(
        before_array, after_array, change_mask,
        regions=change_regions, total_pixels=total_pixels,
    )
    changed_pixels = int(np.sum(change_mask > 127))
    change_pct = (changed_pixels / total_pixels * 100.0) if total_pixels else 0.0
    _prog(95, "Finalizing results")

    stats = {
        "total_pixels": total_pixels,
        "changed_pixels": changed_pixels,
        "unchanged_pixels": total_pixels - changed_pixels,
        "change_percentage": change_pct,
        "image_width": change_mask.shape[1],
        "image_height": change_mask.shape[0],
        "threshold_debug": threshold_debug,
        "alignment_warning": alignment_warning,
        "params": {
            "detection_sensitivity": float(detection_sensitivity),
            "min_region_area": min_region_area,
            "enable_registration": bool(enable_registration),
            "enable_normalization": bool(enable_normalization),
            "registration_ok": bool(registration_ok),
            "registration": reg_meta,
            "inference_mode": inference_mode,
            "windowed": bool(windowed),
            "working_resolution": [int(before_array.shape[1]), int(before_array.shape[0])],
        },
    }
    if score_map is not None:
        stats["_score_map"] = score_map  # numpy; not serialized, consumed downstream

    return change_mask, result_image, stats, change_regions
