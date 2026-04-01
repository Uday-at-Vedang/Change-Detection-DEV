"""
Pothole / road damage detection engine (YOLO-ready).

Uses modular pipeline under app/pothole_detection:
- model_loader.py
- inference.py
- visualization.py
- pothole_detector.py
"""
from __future__ import annotations

import numpy as np
from PIL import Image
import cv2

from .pothole_detection import PotholeDetector


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


def run_pothole_detection(
    before_pil: Image.Image,
    after_pil: Image.Image,
    model_name: str = "Rule-Based v1",
    detection_sensitivity: float = 0.6,
    min_region_area: int | None = None,
):
    """
    Current UI uses (before, after) upload. For potholes, we treat the provided road
    image as the target and run YOLO-style detection.
    """
    img = _preprocess(after_pil)
    # Ultralytics model expects BGR ndarray from OpenCV style pipeline.
    bgr = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)

    # Sensitivity maps to confidence threshold inversely.
    sens = float(np.clip(detection_sensitivity, 0.0, 1.0))
    conf_thr = float(np.clip(0.45 - (sens - 0.5) * 0.35, 0.10, 0.70))
    iou_thr = 0.45
    detector = PotholeDetector(conf_threshold=conf_thr, iou_threshold=iou_thr)
    detections, vis_bgr = detector.run(bgr)
    result = cv2.cvtColor(vis_bgr, cv2.COLOR_BGR2RGB)

    regions = []
    rid = 0
    for d in detections:
        x1, y1, x2, y2 = d["bbox"]
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        area = int(w * h)
        if min_region_area is not None and area < int(min_region_area):
            continue
        rid += 1
        conf = float(d.get("confidence", 0.0))
        severity = "minor"
        area_ratio = area / max(1, img.shape[0] * img.shape[1])
        if area_ratio > 0.01:
            severity = "major"
        elif area_ratio > 0.003:
            severity = "moderate"
        regions.append(
            {
                "id": rid,
                "area": area,
                "bbox": (int(x1), int(y1), int(w), int(h)),
                "center": (int(x1 + w // 2), int(y1 + h // 2)),
                "object_type": "Pothole / Road Damage",
                "confidence": conf,
                "severity": severity,
                "sub_type": str(d.get("class_name", "pothole")),
                "sub_type_confidence": conf,
                "estimated_stories": None,
                "estimated_height_m": None,
                "construction_stage": None,
            }
        )

    total = int(img.shape[0] * img.shape[1])
    changed = int(sum(r["area"] for r in regions))
    stats = {
        "total_pixels": total,
        "changed_pixels": changed,
        "unchanged_pixels": total - changed,
        "change_percentage": (changed / total * 100.0) if total else 0.0,
        "image_width": img.shape[1],
        "image_height": img.shape[0],
        "threshold_debug": {
            "method": f"Pothole Detection ({model_name})",
            "threshold_used": None,
            "confidence_threshold": conf_thr,
            "iou_threshold": iou_thr,
            "sensitivity": sens,
            "detected_boxes": len(regions),
        },
        "params": {
            "detection_sensitivity": sens,
            "min_region_area": int(min_region_area) if min_region_area is not None else None,
            "model_name": model_name,
            "input": "after_only",
        },
    }
    return mask, result, stats, regions

