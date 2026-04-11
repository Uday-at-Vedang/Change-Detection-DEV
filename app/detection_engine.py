"""
Satellite Change Detection Engine v3
High-accuracy detection with multi-channel analysis, SSIM, CVA, texture features,
adaptive thresholding, vegetation/shadow suppression, SNR-weighted fusion,
and improved object classification.
"""
import numpy as np
import cv2
from PIL import Image
from sklearn.cluster import KMeans
from sklearn.preprocessing import StandardScaler
from collections import Counter


# ---------------------------------------------------------------------------
# 1. Pre-processing
# ---------------------------------------------------------------------------

def preprocess_image(image):
    """Preprocess image: convert to RGB, limit size, bilateral denoise."""
    img_array = np.array(image)
    if img_array.ndim == 2:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
    elif img_array.ndim == 3 and img_array.shape[2] == 4:
        img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)
    elif img_array.ndim != 3 or img_array.shape[2] != 3:
        raise ValueError(f"Unsupported image shape: {img_array.shape}")
    max_size = 2000
    height, width = img_array.shape[:2]
    if max(height, width) > max_size:
        scale = max_size / max(height, width)
        new_w, new_h = max(1, int(width * scale)), max(1, int(height * scale))
        img_array = cv2.resize(img_array, (new_w, new_h), interpolation=cv2.INTER_AREA)
    # Bilateral filter: reduces sensor noise while preserving edges
    img_array = cv2.bilateralFilter(img_array, 9, 75, 75)
    return img_array


# ---------------------------------------------------------------------------
# 2. Improved image registration (alignment)
# ---------------------------------------------------------------------------

def register_images(img1, img2, max_features=2000):
    """Align img2 to img1 using ORB + ratio-test + RANSAC homography."""
    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)

    orb = cv2.ORB_create(nfeatures=max_features, scoreType=cv2.ORB_HARRIS_SCORE)
    kp1, des1 = orb.detectAndCompute(gray1, None)
    kp2, des2 = orb.detectAndCompute(gray2, None)

    if des1 is None or des2 is None or len(des1) < 10 or len(des2) < 10:
        return _register_images_ecc_fallback(img1, img2)

    # Use kNN matching with Lowe's ratio test for better matches
    bf = cv2.BFMatcher(cv2.NORM_HAMMING)
    raw_matches = bf.knnMatch(des1, des2, k=2)

    good_matches = []
    for pair in raw_matches:
        if len(pair) == 2:
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

    if len(good_matches) < 10:
        return _register_images_ecc_fallback(img1, img2)

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, 3.0)
    if homography is None:
        return _register_images_ecc_fallback(img1, img2)

    inlier_ratio = np.sum(mask) / len(mask) if mask is not None else 0
    if inlier_ratio < 0.3:
        return _register_images_ecc_fallback(img1, img2)

    # Reject degenerate homographies (near-singular or extreme distortion)
    det = np.linalg.det(homography)
    if abs(det) < 0.1 or abs(det) > 10.0:
        return _register_images_ecc_fallback(img1, img2)

    h, w = img1.shape[:2]
    img2_aligned = cv2.warpPerspective(img2, homography, (w, h), borderMode=cv2.BORDER_REFLECT)
    return img1, img2_aligned, True


def _register_images_ecc_fallback(img1, img2):
    """
    Fallback alignment with ECC affine registration.
    More stable than ORB on low-texture agricultural areas.
    """
    try:
        gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY)
        gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY)
        gray1_f = gray1.astype(np.float32) / 255.0
        gray2_f = gray2.astype(np.float32) / 255.0

        warp = np.eye(2, 3, dtype=np.float32)
        criteria = (
            cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT,
            200,
            1e-6,
        )
        cc, warp = cv2.findTransformECC(
            gray1_f, gray2_f, warp, cv2.MOTION_AFFINE, criteria
        )
        h, w = img1.shape[:2]
        aligned = cv2.warpAffine(
            img2,
            warp,
            (w, h),
            flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP,
            borderMode=cv2.BORDER_REFLECT,
        )
        # Treat as successful only if ECC correlation is reasonable.
        return img1, aligned, bool(cc >= 0.45)
    except Exception:
        return img1, img2, False


# ---------------------------------------------------------------------------
# 3. Improved radiometric normalization
# ---------------------------------------------------------------------------

def normalize_radiometry(img1, img2):
    """Histogram-matching normalization in LAB space. CLAHE applied symmetrically."""
    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    result = lab2.copy()
    for ch in range(3):
        mean1, std1 = np.mean(lab1[:, :, ch]), np.std(lab1[:, :, ch])
        mean2, std2 = np.mean(lab2[:, :, ch]), np.std(lab2[:, :, ch])
        if std2 > 1e-6:
            result[:, :, ch] = (lab2[:, :, ch] - mean2) * (std1 / std2) + mean1

    result_uint8 = np.clip(result, 0, 255).astype(np.uint8)

    # CLAHE on L channel of BOTH images so downstream comparison is symmetric
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    lab1_uint8 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB)
    lab1_uint8[:, :, 0] = clahe.apply(lab1_uint8[:, :, 0])
    result_uint8[:, :, 0] = clahe.apply(result_uint8[:, :, 0])

    img1_out = cv2.cvtColor(lab1_uint8, cv2.COLOR_LAB2RGB)
    img2_out = cv2.cvtColor(result_uint8, cv2.COLOR_LAB2RGB)
    return img1_out, img2_out


# ---------------------------------------------------------------------------
# 4. Vegetation suppression
# ---------------------------------------------------------------------------

def compute_vegetation_mask(img):
    """
    Identify vegetation pixels using pseudo-NDVI and HSV hue/saturation.
    Returns a float map in [0, 1] where 1.0 = vegetation, 0.0 = non-vegetation.
    """
    r = img[:, :, 0].astype(np.float32)
    g = img[:, :, 1].astype(np.float32)
    ndvi = (g - r) / (g + r + 1e-6)

    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0].astype(np.float32)
    sat = hsv[:, :, 1].astype(np.float32)

    ndvi_veg = (ndvi > 0.08).astype(np.float32)
    hsv_veg = ((hue >= 35) & (hue <= 85) & (sat > 30)).astype(np.float32)

    veg = np.clip(ndvi_veg * 0.6 + hsv_veg * 0.4, 0, 1)
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
    """Compute simplified Local Binary Pattern texture descriptor."""
    h, w = gray.shape
    lbp = np.zeros_like(gray, dtype=np.float32)
    for i in range(n_points):
        angle = 2 * np.pi * i / n_points
        dx = int(round(radius * np.cos(angle)))
        dy = int(round(-radius * np.sin(angle)))
        shifted = np.roll(np.roll(gray, dy, axis=0), dx, axis=1)
        lbp += (shifted >= gray).astype(np.float32)
    return lbp / n_points


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


def _ai_fusion_core(img1, img2, sensitivity=0.5):
    """
    Single-pass AI fusion with 5 channels, SNR weighting, and
    vegetation + shadow suppression. Returns (mask, debug).
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    # ---- Channel 1: Multi-scale LAB color difference ----
    lab1 = cv2.cvtColor(img1, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab2 = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB).astype(np.float32)

    scales = [1, 2, 4]
    color_maps = []
    for scale in scales:
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

    # ---- Channel 2: SSIM structural dissimilarity ----
    ssim_change = compute_ssim_change_map(img1, img2)
    ssim_change = ssim_change / (ssim_change.max() + 1e-8)

    # ---- Channel 3: Texture change (LBP) ----
    texture_change = compute_texture_change(img1, img2)
    texture_change = texture_change / (texture_change.max() + 1e-8)

    # ---- Channel 4: Edge change ----
    edge_change = compute_edge_change(img1, img2)

    # ---- Channel 5: Change Vector Analysis ----
    cva_change = compute_cva(img1, img2)

    # ---- SNR-weighted fusion ----
    channels = [color_change, ssim_change, texture_change, edge_change, cva_change]
    weights = [_snr_weight(ch) for ch in channels]
    total_w = sum(weights) + 1e-8
    weights = [w / total_w for w in weights]

    fused = np.zeros_like(color_change, dtype=np.float64)
    for ch, w in zip(channels, weights):
        fused += w * ch.astype(np.float64)

    # ---- Apply vegetation + shadow suppression before thresholding ----
    veg_suppression = compute_combined_vegetation_suppression(img1, img2)
    shadow_suppression = compute_shadow_suppression(img1, img2)
    fused = fused * veg_suppression.astype(np.float64) * shadow_suppression.astype(np.float64)

    # Percentile normalization
    p995 = float(np.quantile(fused, 0.995))
    if p995 <= 1e-8:
        p995 = float(fused.max() + 1e-8)
    fused_norm = np.clip(fused / (p995 + 1e-8), 0.0, 1.0)

    gamma = 0.85
    fused_norm = np.power(fused_norm, gamma)

    fused_smooth = cv2.GaussianBlur(fused_norm.astype(np.float32), (7, 7), 0)

    sens = float(np.clip(sensitivity, 0.0, 1.0))
    q = 0.945 - (sens - 0.5) * 0.04
    q = float(np.clip(q, 0.88, 0.97))

    thr_score = float(np.quantile(fused_smooth, q))
    change_mask = (fused_smooth >= thr_score).astype(np.uint8) * 255

    change_mask = _clean_mask(change_mask, sensitivity=sens)

    change_mask = cv2.bilateralFilter(change_mask, 9, 75, 75)
    _, change_mask = cv2.threshold(change_mask, 127, 255, cv2.THRESH_BINARY)

    debug = {
        "method": "AI-Core",
        "threshold_used": int(thr_score * 255),
        "threshold_percentile_q": q,
        "threshold_score": thr_score,
        "fused_p95": float(np.quantile(fused_smooth, 0.95)),
        "fused_p99": float(np.quantile(fused_smooth, 0.99)),
        "fused_mean": float(np.mean(fused_smooth)),
        "sensitivity": float(sensitivity),
        "channel_weights": {
            "color": round(weights[0], 4),
            "ssim": round(weights[1], 4),
            "texture": round(weights[2], 4),
            "edge": round(weights[3], 4),
            "cva": round(weights[4], 4),
        },
    }
    return change_mask, debug


def ai_deep_learning_method(img1, img2, sensitivity=0.5):
    """
    Uses the pre-trained AdaptFormer model when available; falls back to the
    rule-based multi-channel fusion otherwise.
    """
    from .model_inference import is_model_available, predict_change_mask

    if is_model_available():
        threshold = 0.35 + (1.0 - sensitivity) * 0.3
        try:
            change_mask, score_map = predict_change_mask(
                img1, img2, threshold=threshold)
            change_mask = _clean_mask(change_mask, sensitivity=sensitivity)
            debug = {
                "method": "AI-Based Deep Learning (AdaptFormer)",
                "model": "adaptformer-levir-cd",
                "threshold_used": int(threshold * 255),
                "sensitivity": float(sensitivity),
            }
            return change_mask, debug
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "AdaptFormer inference failed, falling back to rule-based: %s", e)

    change_mask, core_debug = _ai_fusion_core(img1, img2, sensitivity=sensitivity)
    debug = {
        "method": "AI-Based Deep Learning (rule-based fallback)",
        "threshold_used": core_debug.get("threshold_used"),
        "sensitivity": float(sensitivity),
        "core": core_debug,
    }
    return change_mask, debug


def hybrid_method(img1, img2, sensitivity=0.5):
    """Hybrid: weighted fusion of all methods with confidence-based merging."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    diff_mask, diff_debug = image_difference_method(img1, img2, sensitivity=sensitivity)
    feature_mask = feature_based_method(img1, img2)
    ai_mask, ai_debug = ai_deep_learning_method(img1, img2, sensitivity=sensitivity)

    # Weighted combination: AI method gets most weight
    combined = (
        0.2 * diff_mask.astype(np.float32) +
        0.3 * feature_mask.astype(np.float32) +
        0.5 * ai_mask.astype(np.float32)
    )

    # Combined mask values:
    # - diff only: 0.2*255 ≈ 51
    # - feature only: 0.3*255 ≈ 76
    # - ai only: 0.5*255 ≈ 127
    # Keep threshold low enough that ai-only regions can pass.
    base_thr = 98
    sens = float(np.clip(sensitivity, 0.0, 1.0))
    hybrid_thr = int(np.clip(base_thr + int((0.5 - sens) * 36), 60, 150))
    _, final_mask = cv2.threshold(combined.astype(np.uint8), hybrid_thr, 255, cv2.THRESH_BINARY)
    final_mask = _clean_mask(final_mask)
    debug = {
        "method": "Hybrid Approach",
        "threshold_used": int(hybrid_thr),
        "sensitivity": float(sensitivity),
        "sub_methods": {
            "image_difference": diff_debug,
            "ai_deep_learning": ai_debug,
        },
    }
    return final_mask, debug


# ---------------------------------------------------------------------------
# 11. Robust post-processing
# ---------------------------------------------------------------------------

def _clean_mask(mask, sensitivity=0.5, border_margin=12):
    """
    Robust morphological cleaning:
    1. Zero-out border pixels (registration artifacts)
    2. Median filter to kill salt-and-pepper noise
    3. Opening to remove small specks
    4. Closing to bridge tiny gaps
    5. Fill holes inside regions
    6. Erode-then-dilate to break thin noise bridges
    7. Connected-component area + circularity filtering
    """
    mask = mask.copy()
    h, w = mask.shape[:2]

    if border_margin > 0:
        mask[:border_margin, :] = 0
        mask[-border_margin:, :] = 0
        mask[:, :border_margin] = 0
        mask[:, -border_margin:] = 0

    mask = cv2.medianBlur(mask, 5)

    open_size = max(3, int(5 * (1 - sensitivity * 0.5)))
    if open_size % 2 == 0:
        open_size += 1
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)

    close_size = max(3, int(7 * (1 - sensitivity)))
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
    min_component_px = max(80, int(h * w * 0.00004))
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(filled, connectivity=8)
    clean = np.zeros_like(filled)
    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_component_px:
            continue
        cw = stats[i, cv2.CC_STAT_WIDTH]
        ch = stats[i, cv2.CC_STAT_HEIGHT]
        bbox_area = max(cw * ch, 1)
        perimeter_approx = 2 * (cw + ch)
        # Circularity: thin elongated noise has very high perimeter^2/area
        circularity = (perimeter_approx ** 2) / (bbox_area + 1e-8)
        if circularity > 80 and area < min_component_px * 3:
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
        "ndvi": ndvi, "texture_std": texture_std, "lbp_variance": lbp_variance,
        "edge_density": edge_density, "orientation_entropy": orientation_entropy,
        "glcm_contrast": glcm_contrast,
        "color_homogeneity": float(np.mean(std_rgb)),
        "brightness": float(mean_lab[0]),
        "green_ratio": green_ratio, "blue_ratio": blue_ratio, "red_ratio": red_ratio,
        "saturation": float(mean_hsv[1]), "hue": float(mean_hsv[0]),
    }


def _is_transient_object(area, w, h, features):
    """
    Filter out transient objects (people, cars, animals, shadows, etc.)
    that are NOT permanent ground/structural changes.
    Returns True if the region is likely transient and should be excluded.
    """
    aspect_ratio = max(w, h) / max(min(w, h), 1)

    # Very small regions are likely noise, people, or small vehicles
    if area < 300:
        return True

    # Tall narrow regions (aspect > 4) are likely people or poles
    if aspect_ratio > 5.0 and area < 2000:
        return True

    # Very high edge density + small area = likely a person or vehicle
    if features["edge_density"] > 80 and area < 1500:
        return True

    # Extremely high texture variance in small area = likely transient clutter
    if features["texture_std"] > 60 and area < 1000:
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
    hull_a = _rectangular_hull_ratio(gray_a)

    lab_b = cv2.cvtColor(before_crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_a = cv2.cvtColor(after_crop, cv2.COLOR_RGB2LAB).astype(np.float32)
    lab_dist = float(np.mean(np.sqrt(np.sum((lab_a - lab_b) ** 2, axis=2))))

    return {
        "before": feat_b, "after": feat_a,
        "delta_ndvi": feat_a["ndvi"] - feat_b["ndvi"],
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
        "hull_ratio_after": hull_a,
        "lab_color_distance": lab_dist,
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
    if _is_transient_object(area, w, h, feat_a):
        return None, 0.0

    aspect_ratio = max(w, h) / max(min(w, h), 1)
    compactness = (4 * np.pi * area) / ((2 * (w + h)) ** 2 + 1e-6)

    # --- Differential classification when before image is available ---
    diff = None
    if before_region is not None:
        by1 = max(0, y - pad)
        by2 = min(before_region.shape[0], y + h + pad)
        bx1 = max(0, x - pad)
        bx2 = min(before_region.shape[1], x + w + pad)
        before_crop = before_region[by1:by2, bx1:bx2]
        if before_crop.size > 0 and before_crop.shape[0] >= 3 and before_crop.shape[1] >= 3:
            diff = _extract_differential_features(before_crop, after_crop)

    scores = {}

    # ---- Water Body Change ----
    water = 0.0
    if feat_a["blue_ratio"] > 0.36:
        water += 0.22
    if feat_a["texture_std"] < 28:
        water += 0.18
    if feat_a["edge_density"] < 35:
        water += 0.14
    if 90 <= feat_a["hue"] <= 135:
        water += 0.18
    if feat_a["lbp_variance"] < 0.05:
        water += 0.14
    if feat_a["glcm_contrast"] < 500:
        water += 0.10
    if area > 800:
        water += 0.04
    scores["Water Body Change"] = water

    # ---- Vegetation Change ----
    veg = 0.0
    if diff:
        # Differential: detect actual vegetation gain or loss
        if abs(diff["delta_ndvi"]) > 0.08:
            veg += 0.30
        if abs(diff["delta_green_ratio"]) > 0.04:
            veg += 0.20
        if diff["lab_color_distance"] > 15 and (
                diff["before"]["ndvi"] > 0.05 or diff["after"]["ndvi"] > 0.05):
            veg += 0.15
        if abs(diff["delta_saturation"]) > 15 and (
                diff["before"]["green_ratio"] > 0.34 or diff["after"]["green_ratio"] > 0.34):
            veg += 0.15
        if diff["delta_lines"] < 3 and diff["delta_corners"] < 5:
            veg += 0.08
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
        if diff["delta_edge_density"] > 15:
            bld += 0.20
        if diff["delta_orientation_entropy"] < -0.4:
            bld += 0.15
        if diff["delta_lines"] > 5:
            bld += 0.15
        if diff["delta_corners"] > 8:
            bld += 0.12
        if diff["after"]["ndvi"] < 0.05 and diff["before"]["ndvi"] > 0.03:
            bld += 0.12
        if diff["hull_ratio_after"] > 0.55:
            bld += 0.10
        if 1.0 <= aspect_ratio <= 4.0:
            bld += 0.08
        if area > 1000:
            bld += 0.05
    else:
        if feat_a["orientation_entropy"] < 2.5:
            bld += 0.18
        if feat_a["color_homogeneity"] < 28:
            bld += 0.15
        if 1.0 <= aspect_ratio <= 4.0:
            bld += 0.12
        if 0.3 <= compactness <= 0.9:
            bld += 0.10
        if feat_a["edge_density"] > 30:
            bld += 0.12
        if feat_a["glcm_contrast"] > 400:
            bld += 0.10
        if feat_a["saturation"] < 90:
            bld += 0.10
        if 40 <= feat_a["brightness"] <= 90:
            bld += 0.08
        if area > 1000:
            bld += 0.05
    scores["New Construction/Building"] = bld

    # ---- Demolition/Clearing ----
    demo = 0.0
    if diff:
        if diff["delta_edge_density"] < -15:
            demo += 0.22
        if diff["delta_lines"] < -5:
            demo += 0.18
        if diff["delta_corners"] < -8:
            demo += 0.15
        if diff["delta_texture_std"] > 8:
            demo += 0.12
        if diff["delta_brightness"] > 10:
            demo += 0.12
        if diff["after"]["ndvi"] > 0.03 and diff["before"]["ndvi"] < 0.02:
            demo += 0.08
        if area > 800:
            demo += 0.05
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

    best = max(scores, key=scores.get)
    conf = scores[best]

    if conf < 0.30:
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
                     "Road/Pavement Change"}


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
        min_area = int(max(350, min(1400, img_area * 0.00012)))

    for i in range(1, num_labels):
        raw_area = stats[i, cv2.CC_STAT_AREA]
        if raw_area < min_area:
            continue

        x, y, w, h, fill_ratio = _tight_bbox(labels, i, stats[i])

        # Reject very sparse regions (bbox is mostly empty)
        if fill_ratio < 0.12:
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
            # Do not silently drop large coherent regions; keep them as generic
            # ground-change candidates so key changes are still surfaced.
            if raw_area >= max(min_area * 2, 800) and fill_ratio >= 0.18:
                object_type = "Unclassified Ground Change"
                confidence = max(0.2, min(0.5, fill_ratio))
            else:
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
                  detection_sensitivity=0.5, min_region_area=None):
    """Run full detection pipeline; returns change_mask, result_image, stats, regions."""
    before_array = preprocess_image(before_pil)
    after_array = preprocess_image(after_pil)

    registration_ok = False
    if enable_registration:
        before_array, after_array, registration_ok = register_images(before_array, after_array)
    if enable_normalization:
        before_array, after_array = normalize_radiometry(before_array, after_array)

    if method == "AI-Based Deep Learning":
        change_mask, threshold_debug = ai_deep_learning_method(
            before_array, after_array, sensitivity=detection_sensitivity
        )
    elif method == "Image Difference":
        change_mask, threshold_debug = image_difference_method(
            before_array, after_array, sensitivity=detection_sensitivity
        )
    elif method == "Feature-Based":
        change_mask = feature_based_method(before_array, after_array)
        threshold_debug = {
            "method": "Feature-Based",
            "threshold_used": None,
            "note": "KMeans clustering path does not use binary threshold.",
            "sensitivity": float(detection_sensitivity),
        }
    else:
        change_mask, threshold_debug = hybrid_method(
            before_array, after_array, sensitivity=detection_sensitivity
        )

    # --- Adaptive fallback for empty/sparse masks ---
    # In some scenes, ORB/ECC registration + fused thresholding can produce an overly
    # sparse binary mask (leading to 0 detected regions). If that happens, fall back
    # to the more stable Image Difference mask.
    total_pixels = int(change_mask.shape[0] * change_mask.shape[1])
    changed_pixels_ratio = float(np.sum(change_mask > 127)) / float(total_pixels) if total_pixels else 0.0

    used_fallback = False
    if method in ("AI-Based Deep Learning", "Hybrid Approach") and changed_pixels_ratio < 0.0025:
        diff_mask, diff_debug = image_difference_method(
            before_array, after_array, sensitivity=detection_sensitivity
        )
        diff_ratio = float(np.sum(diff_mask > 127)) / float(total_pixels) if total_pixels else 0.0
        # Only switch if the diff mask clearly contains more signal.
        if diff_ratio > max(0.005, changed_pixels_ratio * 3.0):
            change_mask = diff_mask
            used_fallback = True
            threshold_debug = {
                "method": f"{method} (fallback->Image Difference)",
                "fallback_used": True,
                "ai_hybrid_changed_ratio": changed_pixels_ratio,
                "diff_changed_ratio": diff_ratio,
                "diff_debug": diff_debug,
                "sensitivity": float(detection_sensitivity),
            }

    change_regions = analyze_change_regions(
        change_mask,
        after_array,
        min_area=min_region_area,
        before_img=before_array,
        registration_ok=registration_ok,
    )

    total_pixels = int(change_mask.shape[0] * change_mask.shape[1])
    result_image = visualize_changes(
        before_array, after_array, change_mask,
        regions=change_regions, total_pixels=total_pixels
    )
    changed_pixels = int(np.sum(change_mask > 127))
    change_pct = (changed_pixels / total_pixels * 100.0) if total_pixels else 0.0

    stats = {
        "total_pixels": total_pixels,
        "changed_pixels": changed_pixels,
        "unchanged_pixels": total_pixels - changed_pixels,
        "change_percentage": change_pct,
        "image_width": change_mask.shape[1],
        "image_height": change_mask.shape[0],
        "threshold_debug": threshold_debug,
        "params": {
            "detection_sensitivity": float(detection_sensitivity),
            "min_region_area": min_region_area,
            "enable_registration": bool(enable_registration),
            "enable_normalization": bool(enable_normalization),
            "registration_ok": bool(registration_ok),
        },
    }

    return change_mask, result_image, stats, change_regions
