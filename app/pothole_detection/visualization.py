from __future__ import annotations

from typing import List, Dict

import cv2
import numpy as np


def draw_pothole_boxes(image_bgr: np.ndarray, detections: List[Dict]) -> np.ndarray:
    """
    Draw red bounding boxes with confidence labels.
    """
    out = image_bgr.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        conf = float(det.get("confidence", 0.0))
        label = f"pothole {conf:.2f}"

        # Red box (BGR)
        cv2.rectangle(out, (x1, y1), (x2, y2), (0, 0, 255), 2)

        # Label background
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        y_text = max(16, y1 - 6)
        cv2.rectangle(out, (x1, y_text - th - 6), (x1 + tw + 8, y_text + 2), (0, 0, 255), -1)
        cv2.putText(out, label, (x1 + 4, y_text - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    return out

