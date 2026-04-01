"""
Pothole / road damage detection starter engine.

Goal: separate pipeline that can evolve to a real model (YOLO/Mask R-CNN/SegFormer).
This initial version is a CPU-friendly heuristic detector designed for vehicle/drone imagery.

Notes:
- Satellite imagery is generally too coarse for potholes unless very high resolution (<10 cm/px).
- Vehicle camera or low-altitude drone is the realistic input for pothole detection.
"""
from __future__ import annotations

import cv2
import numpy as np
from PIL import Image


def _preprocess(image: Image.Image, max_size: int = 1600) -> np.ndarray:
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


def _road_texture_response(gray: np.ndarray) -> np.ndarray:
    # Potholes often appear as dark regions with sharp boundaries + rough texture.
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    lap = cv2.Laplacian(blur, cv2.CV_32F, ksize=3)
    rough = cv2.GaussianBlur(np.abs(lap), (7, 7), 0)
    return _norm01(rough)


def _shadow_score(gray: np.ndarray) -> np.ndarray:
    # Darker-than-local background regions.
    local = cv2.GaussianBlur(gray, (31, 31), 0)
    diff = np.clip((local - gray).astype(np.float32), 0, None)
    return _norm01(diff)


def _edge_score(gray: np.ndarray) -> np.ndarray:
    med = float(np.median(gray))
    t1 = int(max(0, 0.66 * med))
    t2 = int(min(255, 1.33 * med))
    edges = cv2.Canny(gray, t1, t2)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    return edges.astype(np.float32) / 255.0


def _clean(mask: np.ndarray) -> np.ndarray:
    m = mask.copy()
    m = cv2.medianBlur(m, 5)
    k_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    k_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k_open)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k_close)
    return m


def _extract_regions(mask: np.ndarray, min_area: int = 220):
    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape[:2]
    img_area = h * w
    regs = []
    rid = 0
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < min_area:
            continue
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        bw = int(stats[i, cv2.CC_STAT_WIDTH])
        bh = int(stats[i, cv2.CC_STAT_HEIGHT])
        if bw * bh > img_area * 0.25:
            continue
        ar = max(bw, bh) / max(1, min(bw, bh))
        if ar > 6.0:
            continue
        cx, cy = cents[i]
        fill = area / max(1, bw * bh)
        conf = float(np.clip(0.25 + fill * 0.7, 0.25, 0.95))
        sev = "minor"
        if area / img_area > 0.01:
            sev = "major"
        elif area / img_area > 0.003:
            sev = "moderate"
        rid += 1
        regs.append(
            {
                "id": rid,
                "area": area,
                "bbox": (x, y, bw, bh),
                "center": (int(cx), int(cy)),
                "object_type": "Pothole / Road Damage",
                "confidence": conf,
                "severity": sev,
                "sub_type": "Pothole",
                "sub_type_confidence": conf,
                "estimated_stories": None,
                "estimated_height_m": None,
                "construction_stage": None,
            }
        )
    regs.sort(key=lambda r: r["area"], reverse=True)
    return regs[:80]


def _visualize(img: np.ndarray, mask: np.ndarray, regions: list[dict]) -> np.ndarray:
    out = img.copy().astype(np.float32)
    m = (mask > 127).astype(np.float32)
    # Orange overlay for road damage
    layer = np.zeros_like(out)
    layer[:, :, 0] = 255
    layer[:, :, 1] = 165
    alpha = 0.35
    for c in range(3):
        out[:, :, c] = out[:, :, c] * (1 - m * alpha) + layer[:, :, c] * (m * alpha)
    vis = np.clip(out, 0, 255).astype(np.uint8)
    for r in regions:
        x, y, w, h = r["bbox"]
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 140, 255), 2)
        cv2.putText(vis, str(r["id"]), (x + 4, max(14, y - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return vis


def run_pothole_detection(
    before_pil: Image.Image,
    after_pil: Image.Image,
    model_name: str = "Rule-Based v1",
    detection_sensitivity: float = 0.6,
    min_region_area: int | None = None,
):
    """
    Current UI uses (before, after) upload. For potholes, we treat the *after* image as
    the road image and ignore the before image.
    """
    img = _preprocess(after_pil)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    rough = _road_texture_response(gray)
    shadow = _shadow_score(gray)
    edges = _edge_score(gray)

    fused = 0.45 * shadow + 0.35 * rough + 0.20 * edges
    fused = cv2.GaussianBlur(fused.astype(np.float32), (7, 7), 0)

    sens = float(np.clip(detection_sensitivity, 0.0, 1.0))
    q = float(np.clip(0.975 - (sens - 0.5) * 0.10, 0.85, 0.985))
    thr = float(np.quantile(fused, q))
    mask = (fused >= thr).astype(np.uint8) * 255
    mask = _clean(mask)

    if min_region_area is None:
        min_region_area = int(max(150, min(1200, mask.shape[0] * mask.shape[1] * 0.00005)))
    regions = _extract_regions(mask, min_area=int(min_region_area))
    result = _visualize(img, mask, regions)

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
            "method": f"Pothole Detection ({model_name})",
            "threshold_used": int(np.clip(thr * 255.0, 0, 255)),
            "threshold_percentile_q": q,
            "sensitivity": sens,
        },
        "params": {
            "detection_sensitivity": sens,
            "min_region_area": int(min_region_area),
            "model_name": model_name,
            "input": "after_only",
        },
    }
    return mask, result, stats, regions

