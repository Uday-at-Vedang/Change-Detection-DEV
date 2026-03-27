"""
Landslide Detection Engine (Uttarakhand-focused starter).

This module is intentionally separate from the generic change detection engine.
It uses landslide-oriented cues from before/after optical imagery:
- vegetation loss
- bare-soil increase
- texture roughness change
- edge disruption
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _preprocess(image: Image.Image, max_size: int = 2200) -> np.ndarray:
    arr = np.array(image.convert("RGB"))
    h, w = arr.shape[:2]
    if max(h, w) > max_size:
        s = max_size / max(h, w)
        arr = cv2.resize(arr, (max(1, int(w * s)), max(1, int(h * s))), interpolation=cv2.INTER_AREA)
    return arr


def _norm01(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    lo = float(np.min(x))
    hi = float(np.max(x))
    if hi - lo < 1e-8:
        return np.zeros_like(x, dtype=np.float32)
    return (x - lo) / (hi - lo)


def _green_index(rgb: np.ndarray) -> np.ndarray:
    # RGB proxy for vegetation index when NIR is unavailable.
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    return (g - r) / (g + r + 1e-6)


def _soil_score(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1] / 255.0
    v = hsv[:, :, 2] / 255.0
    # Dry/bare soil often: warm hue, medium saturation, medium/high brightness.
    warm = ((h >= 8) & (h <= 38)).astype(np.float32)
    sat = np.clip(1.0 - np.abs(s - 0.45) / 0.45, 0, 1)
    bri = np.clip((v - 0.25) / 0.75, 0, 1)
    return _norm01(0.5 * warm + 0.25 * sat + 0.25 * bri)


def _texture_roughness(gray: np.ndarray) -> np.ndarray:
    lap = cv2.Laplacian(gray, cv2.CV_32F, ksize=3)
    rough = cv2.GaussianBlur(np.abs(lap), (5, 5), 0)
    return _norm01(rough)


def _edge_change(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    g1 = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY)
    g2 = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY)
    e1 = cv2.Canny(g1, 60, 140)
    e2 = cv2.Canny(g2, 60, 140)
    diff = cv2.absdiff(e1, e2).astype(np.float32) / 255.0
    return cv2.GaussianBlur(diff, (5, 5), 0)


def _clean(mask: np.ndarray) -> np.ndarray:
    m = mask.copy()
    h, w = m.shape[:2]
    b = max(8, int(min(h, w) * 0.01))
    m[:b, :] = 0
    m[-b:, :] = 0
    m[:, :b] = 0
    m[:, -b:] = 0
    m = cv2.medianBlur(m, 5)
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k_open)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_close)
    return m


def _extract_regions(mask: np.ndarray, after: np.ndarray, min_area: int = 350):
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape[:2]
    img_area = h * w
    regions = []
    rid = 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if bw * bh > img_area * 0.9:
            continue
        cx, cy = cents[i]
        ratio = area / max(1, bw * bh)
        conf = float(np.clip(0.25 + ratio * 0.65, 0.25, 0.95))
        sev = "minor"
        if area / img_area > 0.02:
            sev = "major"
        elif area / img_area > 0.006:
            sev = "moderate"
        rid += 1
        regions.append(
            {
                "id": rid,
                "area": area,
                "bbox": (x, y, bw, bh),
                "center": (int(cx), int(cy)),
                "object_type": "Landslide Suspected Zone",
                "confidence": conf,
                "severity": sev,
                "sub_type": "Debris / Slope Failure",
                "sub_type_confidence": conf,
                "estimated_stories": None,
                "estimated_height_m": None,
                "construction_stage": None,
            }
        )
    return regions[:80]


def _visualize(after: np.ndarray, mask: np.ndarray, regions: list[dict]) -> np.ndarray:
    out = after.copy().astype(np.float32)
    m = (mask > 127).astype(np.float32)
    amber = np.zeros_like(out)
    amber[:, :, 0] = 255  # R
    amber[:, :, 1] = 165  # G
    alpha = 0.35
    for c in range(3):
        out[:, :, c] = out[:, :, c] * (1 - m * alpha) + amber[:, :, c] * (m * alpha)
    vis = np.clip(out, 0, 255).astype(np.uint8)
    for r in regions:
        x, y, w, h = r["bbox"]
        color = (0, 140, 255)  # BGR-like style for warning tone in RGB draw context
        cv2.rectangle(vis, (x, y), (x + w, y + h), color, 2)
        label = f'{r["id"]}'
        cv2.putText(vis, label, (x + 4, max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def run_landslide_detection(
    before_pil: Image.Image,
    after_pil: Image.Image,
    model_name: str = "Rule-Based v1",
    detection_sensitivity: float = 0.6,
    min_region_area: int | None = None,
):
    """
    Returns: change_mask, result_image, stats, regions.
    """
    before = _preprocess(before_pil)
    after = _preprocess(after_pil)
    if before.shape != after.shape:
        after = cv2.resize(after, (before.shape[1], before.shape[0]), interpolation=cv2.INTER_LINEAR)

    g_before = _green_index(before)
    g_after = _green_index(after)
    veg_loss = _norm01(np.clip(g_before - g_after, 0, None))

    soil_before = _soil_score(before)
    soil_after = _soil_score(after)
    soil_gain = _norm01(np.clip(soil_after - soil_before, 0, None))

    gray_before = cv2.cvtColor(before, cv2.COLOR_RGB2GRAY).astype(np.float32)
    gray_after = cv2.cvtColor(after, cv2.COLOR_RGB2GRAY).astype(np.float32)
    rough_before = _texture_roughness(gray_before)
    rough_after = _texture_roughness(gray_after)
    rough_change = _norm01(np.abs(rough_after - rough_before))

    edge_change = _edge_change(before, after)

    sens = float(np.clip(detection_sensitivity, 0.0, 1.0))
    # Landslide-oriented fusion
    fused = (
        0.38 * veg_loss
        + 0.30 * soil_gain
        + 0.20 * rough_change
        + 0.12 * edge_change
    )
    fused = cv2.GaussianBlur(fused.astype(np.float32), (7, 7), 0)

    # Higher sensitivity => lower quantile threshold.
    q = float(np.clip(0.965 - (sens - 0.5) * 0.08, 0.88, 0.98))
    thr = float(np.quantile(fused, q))
    mask = (fused >= thr).astype(np.uint8) * 255
    mask = _clean(mask)

    if min_region_area is None:
        min_region_area = int(max(250, min(1400, mask.shape[0] * mask.shape[1] * 0.00010)))
    regions = _extract_regions(mask, after, min_area=int(min_region_area))
    result = _visualize(after, mask, regions)

    total = int(mask.shape[0] * mask.shape[1])
    changed = int(np.sum(mask > 127))
    stats = {
        "total_pixels": total,
        "changed_pixels": changed,
        "unchanged_pixels": total - changed,
        "change_percentage": (changed / total * 100.0) if total else 0.0,
        "image_width": mask.shape[1],
        "image_height": mask.shape[0],
        "threshold_debug": {
            "method": f"Landslide Detection ({model_name})",
            "threshold_used": int(np.clip(thr * 255.0, 0, 255)),
            "threshold_percentile_q": q,
            "sensitivity": sens,
        },
        "params": {
            "detection_sensitivity": sens,
            "min_region_area": int(min_region_area),
            "model_name": model_name,
        },
    }
    return mask, result, stats, regions

