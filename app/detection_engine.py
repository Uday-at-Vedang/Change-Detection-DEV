"""
Satellite Change Detection Engine v2
High-accuracy detection with multi-channel analysis, SSIM, texture features,
adaptive thresholding, and improved object classification.
"""
import io
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
    """Preprocess image: convert to RGB, limit size."""
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
        return img1, img2, False

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
        return img1, img2, False

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(dst_pts, src_pts, cv2.RANSAC, 3.0)
    if homography is None:
        return img1, img2, False

    inlier_ratio = np.sum(mask) / len(mask) if mask is not None else 0
    if inlier_ratio < 0.3:
        return img1, img2, False

    # Reject degenerate homographies (near-singular or extreme distortion)
    det = np.linalg.det(homography)
    if abs(det) < 0.1 or abs(det) > 10.0:
        return img1, img2, False

    h, w = img1.shape[:2]
    img2_aligned = cv2.warpPerspective(img2, homography, (w, h), borderMode=cv2.BORDER_REFLECT)
    return img1, img2_aligned, True


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
# 4. SSIM-based structural change map
# ---------------------------------------------------------------------------

def compute_ssim_change_map(img1, img2, win_size=7):
    """Compute per-pixel structural dissimilarity (1 - SSIM)."""
    gray1 = cv2.cvtColor(img1, cv2.COLOR_RGB2GRAY).astype(np.float64)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_RGB2GRAY).astype(np.float64)

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    mu1 = cv2.GaussianBlur(gray1, (win_size, win_size), 1.5)
    mu2 = cv2.GaussianBlur(gray2, (win_size, win_size), 1.5)

    mu1_sq = mu1 * mu1
    mu2_sq = mu2 * mu2
    mu1_mu2 = mu1 * mu2

    # Clamp to zero: E[X²]-E[X]² can go slightly negative from float rounding
    sigma1_sq = np.maximum(cv2.GaussianBlur(gray1 * gray1, (win_size, win_size), 1.5) - mu1_sq, 0)
    sigma2_sq = np.maximum(cv2.GaussianBlur(gray2 * gray2, (win_size, win_size), 1.5) - mu2_sq, 0)
    sigma12 = cv2.GaussianBlur(gray1 * gray2, (win_size, win_size), 1.5) - mu1_mu2

    denom = (mu1_sq + mu2_sq + C1) * (sigma1_sq + sigma2_sq + C2)
    ssim_map = ((2 * mu1_mu2 + C1) * (2 * sigma12 + C2)) / (denom + 1e-12)

    # Structural dissimilarity: 0 = identical, 1 = completely different
    dssim = np.clip((1.0 - ssim_map) / 2.0, 0, 1)
    return dssim


# ---------------------------------------------------------------------------
# 5. Texture feature extraction (LBP)
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
# 6. Edge-aware change detection
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
# 7. Improved detection methods
# ---------------------------------------------------------------------------

def image_difference_method(img1, img2, threshold=0.25, blur_size=5):
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

    # Adaptive threshold using Otsu on the change map
    delta_uint8 = (delta_e * 255).astype(np.uint8)
    _, change_mask = cv2.threshold(delta_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    change_mask = _clean_mask(change_mask)
    return change_mask


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


def ai_deep_learning_method(img1, img2):
    """
    Advanced multi-signal fusion:
    - Multi-scale color difference (LAB)
    - Structural dissimilarity (SSIM)
    - Texture change (LBP)
    - Edge change (Canny)
    All fused with learned weights and adaptive thresholding.
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
        # Delta-E (CIE76) normalized
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

    # ---- Adaptive fusion ----
    # Weight channels by their discriminative power (entropy-based)
    channels = [color_change, ssim_change, texture_change, edge_change]
    weights = []
    for ch in channels:
        ch_uint8 = (ch * 255).astype(np.uint8)
        hist = cv2.calcHist([ch_uint8], [0], None, [256], [0, 256]).flatten()
        hist = hist / (hist.sum() + 1e-8)
        entropy = -np.sum(hist[hist > 0] * np.log2(hist[hist > 0] + 1e-10))
        weights.append(entropy)

    # Normalize weights
    total_w = sum(weights) + 1e-8
    weights = [w / total_w for w in weights]

    # Fuse
    fused = np.zeros_like(color_change, dtype=np.float64)
    for ch, w in zip(channels, weights):
        fused += w * ch.astype(np.float64)

    fused = fused / (fused.max() + 1e-8)
    fused_uint8 = (fused * 255).astype(np.uint8)

    # Adaptive threshold: Otsu + refinement
    _, change_mask = cv2.threshold(fused_uint8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    # Post-process
    change_mask = _clean_mask(change_mask)

    # Edge-preserving smoothing on the mask
    change_mask = cv2.bilateralFilter(change_mask, 9, 75, 75)
    _, change_mask = cv2.threshold(change_mask, 127, 255, cv2.THRESH_BINARY)

    return change_mask


def hybrid_method(img1, img2):
    """Hybrid: weighted fusion of all methods with confidence-based merging."""
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    diff_mask = image_difference_method(img1, img2)
    feature_mask = feature_based_method(img1, img2)
    ai_mask = ai_deep_learning_method(img1, img2)

    # Weighted combination: AI method gets most weight
    combined = (
        0.2 * diff_mask.astype(np.float32) +
        0.3 * feature_mask.astype(np.float32) +
        0.5 * ai_mask.astype(np.float32)
    )

    _, final_mask = cv2.threshold(combined.astype(np.uint8), 127, 255, cv2.THRESH_BINARY)
    final_mask = _clean_mask(final_mask)
    return final_mask


# ---------------------------------------------------------------------------
# 8. Robust post-processing
# ---------------------------------------------------------------------------

def _clean_mask(mask, sensitivity=0.5):
    """Adaptive morphological cleaning: close gaps, remove noise, fill holes."""
    # Close small gaps
    close_size = max(3, int(7 * (1 - sensitivity)))
    if close_size % 2 == 0:
        close_size += 1
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (close_size, close_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel_close)

    # Remove small noise
    open_size = 3
    kernel_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (open_size, open_size))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel_open)

    # Fill small holes inside detected regions
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)

    return filled


# ---------------------------------------------------------------------------
# 9. Severity classification and improved visualization
# ---------------------------------------------------------------------------

def _severity_from_region(region, total_pixels):
    """
    Classify change severity from area and confidence.
    Green = minor, Yellow = moderate, Red = major.
    Used for color-coded bounding boxes and table summary.
    """
    area = region.get("area", 0)
    confidence = region.get("confidence", 0.0)
    if total_pixels <= 0:
        return "minor"
    area_ratio = area / total_pixels
    # Combined score: larger area and higher confidence -> more severe
    score = area_ratio * 500 + confidence * 2
    if score < 0.5:
        return "minor"
    if score < 1.2:
        return "moderate"
    return "major"


# BGR colors for severity (OpenCV uses BGR)
_SEVERITY_COLORS = {
    "minor": (0, 200, 0),      # Green
    "moderate": (0, 255, 255), # Yellow
    "major": (0, 0, 255),      # Red
}


def visualize_changes(img1, img2, change_mask, regions=None, total_pixels=None):
    """
    Overlay change mask on 'after' image; draw color-coded bounding boxes
    by severity (green=minor, yellow=moderate, red=major) and numbered labels.
    """
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))
    if change_mask.shape[:2] != img2.shape[:2]:
        change_mask = cv2.resize(change_mask, (img2.shape[1], img2.shape[0]))

    overlay = img2.copy().astype(np.float32)
    mask_bool = change_mask > 127
    mask_float = mask_bool.astype(np.float32)

    # Red overlay for all detected change pixels
    red_layer = np.zeros_like(img2, dtype=np.float32)
    red_layer[:, :, 0] = 255
    alpha = 0.50
    for c in range(3):
        overlay[:, :, c] = overlay[:, :, c] * (1 - mask_float * alpha) + red_layer[:, :, c] * mask_float * alpha

    total_px = total_pixels if total_pixels is not None else (img2.shape[0] * img2.shape[1])

    if regions:
        overlay_uint8 = np.clip(overlay, 0, 255).astype(np.uint8)
        for r in regions:
            x, y, w, h = r["bbox"]
            severity = r.get("severity") or _severity_from_region(r, total_px)
            color = _SEVERITY_COLORS.get(severity, (255, 255, 255))

            # Color-coded bounding box (thicker for visibility)
            cv2.rectangle(overlay_uint8, (x, y), (x + w, y + h), color, 2)

            # Numbered label matching summary table (region ID)
            rid = r.get("id", 0)
            label = str(rid)
            font = cv2.FONT_HERSHEY_SIMPLEX
            font_scale = max(0.4, min(0.7, w / 150))
            thickness = 2
            (tw, th), _ = cv2.getTextSize(label, font, font_scale, thickness)
            lx, ly = x, max(th + 4, y - 4)
            cv2.rectangle(overlay_uint8, (lx, ly - th - 4), (lx + tw + 8, ly + 2), (0, 0, 0), cv2.FILLED)
            cv2.putText(overlay_uint8, label, (lx + 4, ly - 2), font, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
        return overlay_uint8

    return np.clip(overlay, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 10. Improved object classification
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


# Ground-level change categories only
GROUND_CHANGE_TYPES = [
    "New Construction/Building",
    "Demolition/Clearing",
    "Vegetation Change",
    "Water Body Change",
    "Road/Pavement Change",
    "Bare Land/Soil Change",
]


def classify_object_type(image_region, bbox):
    """
    Classify GROUND-LEVEL structural changes only.
    Categories: construction, demolition, vegetation, water, road, bare land.
    Transient objects (people, cars, animals) are filtered out.
    """
    x, y, w, h = bbox
    pad = 5
    y1 = max(0, y - pad)
    y2 = min(image_region.shape[0], y + h + pad)
    x1 = max(0, x - pad)
    x2 = min(image_region.shape[1], x + w + pad)
    region = image_region[y1:y2, x1:x2]

    if region.size == 0 or region.shape[0] < 3 or region.shape[1] < 3:
        return "Unclassified", 0.0

    features = extract_advanced_features(region)
    if features is None:
        return "Unclassified", 0.0

    area = w * h

    # Filter out transient objects (people, cars, animals)
    if _is_transient_object(area, w, h, features):
        return None, 0.0  # signal to exclude this region

    aspect_ratio = max(w, h) / max(min(w, h), 1)
    compactness = (4 * np.pi * area) / ((2 * (w + h)) ** 2 + 1e-6)

    scores = {}

    # ---- Water Body Change ----
    water = 0.0
    if features["blue_ratio"] > 0.36:
        water += 0.22
    if features["texture_std"] < 28:
        water += 0.18
    if features["edge_density"] < 35:
        water += 0.14
    if 90 <= features["hue"] <= 135:
        water += 0.18
    if features["lbp_variance"] < 0.05:
        water += 0.14
    if features["glcm_contrast"] < 500:
        water += 0.10
    if area > 800:
        water += 0.04
    scores["Water Body Change"] = water

    # ---- Vegetation Change (deforestation, new growth, crop change) ----
    veg = 0.0
    if features["ndvi"] > 0.05:
        veg += 0.22
    if features["ndvi"] > 0.15:
        veg += 0.10
    if features["green_ratio"] > 0.36:
        veg += 0.18
    if 35 <= features["hue"] <= 85:
        veg += 0.15
    if features["texture_std"] > 18:
        veg += 0.08
    if features["lbp_variance"] > 0.03:
        veg += 0.08
    if features["saturation"] > 40:
        veg += 0.10
    if features["orientation_entropy"] > 2.5:
        veg += 0.05
    if area > 500:
        veg += 0.04
    scores["Vegetation Change"] = veg

    # ---- New Construction/Building ----
    bld = 0.0
    if features["orientation_entropy"] < 2.5:
        bld += 0.18
    if features["color_homogeneity"] < 28:
        bld += 0.15
    if 1.0 <= aspect_ratio <= 4.0:
        bld += 0.12
    if 0.3 <= compactness <= 0.9:
        bld += 0.10
    if features["edge_density"] > 30:
        bld += 0.12
    if features["glcm_contrast"] > 400:
        bld += 0.10
    if features["saturation"] < 90:
        bld += 0.10
    if 40 <= features["brightness"] <= 90:
        bld += 0.08
    if area > 1000:
        bld += 0.05
    scores["New Construction/Building"] = bld

    # ---- Demolition/Clearing ----
    demo = 0.0
    if features["texture_std"] > 30:
        demo += 0.18
    if features["orientation_entropy"] > 2.8:
        demo += 0.15
    if features["color_homogeneity"] > 25:
        demo += 0.15
    if features["brightness"] > 60:
        demo += 0.10
    if features["ndvi"] < 0.05:
        demo += 0.12
    if features["saturation"] < 70:
        demo += 0.10
    if area > 800:
        demo += 0.05
    scores["Demolition/Clearing"] = demo

    # ---- Road/Pavement Change ----
    road = 0.0
    if aspect_ratio > 2.5:
        road += 0.22
    if features["color_homogeneity"] < 22:
        road += 0.18
    if features["texture_std"] < 32:
        road += 0.15
    if features["saturation"] < 65:
        road += 0.12
    if features["orientation_entropy"] < 2.0:
        road += 0.15
    if 35 <= features["brightness"] <= 75:
        road += 0.10
    if compactness < 0.3:
        road += 0.05
    if area > 600:
        road += 0.03
    scores["Road/Pavement Change"] = road

    # ---- Bare Land/Soil Change ----
    soil = 0.0
    if features["red_ratio"] > 0.34 and features["green_ratio"] < 0.36:
        soil += 0.20
    if 8 <= features["hue"] <= 38:
        soil += 0.18
    if features["ndvi"] < 0.05:
        soil += 0.18
    if features["texture_std"] < 35:
        soil += 0.12
    if features["lbp_variance"] < 0.04:
        soil += 0.12
    if 40 <= features["saturation"] <= 130:
        soil += 0.10
    if 45 <= features["brightness"] <= 82:
        soil += 0.10
    scores["Bare Land/Soil Change"] = soil

    # Use raw scores as confidence (each rule set sums to ~1.0 max)
    # Do NOT normalize by max_score — that inflates weak matches to 1.0
    best = max(scores, key=scores.get)
    conf = scores[best]

    if conf < 0.30:
        return "Unclassified", conf
    return best, min(conf, 1.0)


def classify_with_ensemble(image_region, bbox, num_sub=4):
    """Ensemble: classify full region + sub-regions, vote with confidence weighting."""
    x, y, w, h = bbox
    sub_boxes = [(x, y, w, h)]  # full region

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
        obj_type, conf = classify_object_type(image_region, sb)
        if obj_type is None:
            transient_count += 1
            continue
        if obj_type != "Unclassified":
            classifications.append(obj_type)
            confidences.append(conf)

    # Only exclude if majority of sub-regions are transient
    if transient_count > len(sub_boxes) // 2:
        return None, 0.0

    if not classifications:
        return classify_object_type(image_region, (x, y, w, h))

    # Weighted voting
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
# 11. Vegetation sub-classification
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
# 12. Structural change sub-classification
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
# 13. 3D Building Analysis — height estimation + construction stage
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
# 14. Region analysis
# ---------------------------------------------------------------------------

def analyze_change_regions(change_mask, image, min_area=200, use_ensemble=True,
                           before_img=None):
    """
    Find connected change regions, classify as ground-level changes only.
    Transient objects (people, cars, animals) are filtered out.
    Building regions get enriched with 3D analysis (stories, height, stage).
    """
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(change_mask, connectivity=8)
    change_regions = []
    region_id = 0

    for i in range(1, num_labels):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < min_area:
            continue

        x = stats[i, cv2.CC_STAT_LEFT]
        y = stats[i, cv2.CC_STAT_TOP]
        w = stats[i, cv2.CC_STAT_WIDTH]
        h = stats[i, cv2.CC_STAT_HEIGHT]
        cx, cy = centroids[i]

        if use_ensemble and area > 500:
            object_type, confidence = classify_with_ensemble(image, (x, y, w, h))
        else:
            object_type, confidence = classify_object_type(image, (x, y, w, h))

        if object_type is None:
            continue

        region_id += 1
        region = {
            "id": region_id,
            "area": area,
            "bbox": (x, y, w, h),
            "center": (int(cx), int(cy)),
            "object_type": object_type,
            "confidence": confidence,
            "sub_type": None,
            "sub_type_confidence": None,
            "estimated_stories": None,
            "estimated_height_m": None,
            "construction_stage": None,
        }

        # Sub-classification and 3D analysis require before image
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

                # 3D analysis for building/construction regions
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

    change_regions.sort(key=lambda r: r["area"], reverse=True)

    # Assign severity for color-coded display and table summary
    total_px = change_mask.shape[0] * change_mask.shape[1]
    for r in change_regions:
        r["severity"] = _severity_from_region(r, total_px)

    return change_regions


# ---------------------------------------------------------------------------
# 15. Main pipeline
# ---------------------------------------------------------------------------

def run_detection(before_pil, after_pil, method="AI-Based Deep Learning",
                  enable_registration=True, enable_normalization=True):
    """Run full detection pipeline; returns change_mask, result_image, stats, regions."""
    before_array = preprocess_image(before_pil)
    after_array = preprocess_image(after_pil)

    if enable_registration:
        before_array, after_array, _ = register_images(before_array, after_array)
    if enable_normalization:
        before_array, after_array = normalize_radiometry(before_array, after_array)

    if method == "AI-Based Deep Learning":
        change_mask = ai_deep_learning_method(before_array, after_array)
    elif method == "Image Difference":
        change_mask = image_difference_method(before_array, after_array)
    elif method == "Feature-Based":
        change_mask = feature_based_method(before_array, after_array)
    else:
        change_mask = hybrid_method(before_array, after_array)

    change_regions = analyze_change_regions(
        change_mask, after_array, min_area=200, before_img=before_array
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
    }

    return change_mask, result_image, stats, change_regions
