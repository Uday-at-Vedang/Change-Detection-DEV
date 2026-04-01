from __future__ import annotations

from typing import List, Dict

import numpy as np


def run_pothole_inference(
    model,
    image_bgr: np.ndarray,
    conf_threshold: float = 0.25,
    iou_threshold: float = 0.45,
) -> List[Dict]:
    """
    Run YOLO inference and normalize predictions to a simple list format.
    """
    results = model.predict(
        source=image_bgr,
        conf=conf_threshold,
        iou=iou_threshold,
        verbose=False,
    )
    preds: List[Dict] = []
    if not results:
        return preds

    r = results[0]
    names = getattr(r, "names", {}) or {}
    boxes = getattr(r, "boxes", None)
    if boxes is None:
        return preds

    xyxy = boxes.xyxy.cpu().numpy() if hasattr(boxes.xyxy, "cpu") else boxes.xyxy
    confs = boxes.conf.cpu().numpy() if hasattr(boxes.conf, "cpu") else boxes.conf
    clss = boxes.cls.cpu().numpy() if hasattr(boxes.cls, "cpu") else boxes.cls

    for i in range(len(xyxy)):
        x1, y1, x2, y2 = [int(v) for v in xyxy[i]]
        confidence = float(confs[i])
        cls_id = int(clss[i]) if clss is not None else 0
        cls_name = names.get(cls_id, "pothole")
        preds.append(
            {
                "bbox": [x1, y1, x2, y2],
                "confidence": confidence,
                "class_id": cls_id,
                "class_name": str(cls_name),
            }
        )

    return preds

