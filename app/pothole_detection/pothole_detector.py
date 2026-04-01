from __future__ import annotations

from typing import Dict, Any, List

import cv2
import numpy as np

from .model_loader import get_yolo_model
from .inference import run_pothole_inference
from .visualization import draw_pothole_boxes


class PotholeDetector:
    """
    Modular pothole detector:
    - preprocessing
    - model inference
    - post-processing
    - visualization
    """

    def __init__(self, conf_threshold: float = 0.25, iou_threshold: float = 0.45):
        self.conf_threshold = float(conf_threshold)
        self.iou_threshold = float(iou_threshold)
        self.model = get_yolo_model()

    def preprocess(self, image_bgr: np.ndarray) -> np.ndarray:
        # Lightweight denoise for road textures
        return cv2.bilateralFilter(image_bgr, 5, 35, 35)

    def infer(self, image_bgr: np.ndarray) -> List[Dict[str, Any]]:
        return run_pothole_inference(
            self.model,
            image_bgr,
            conf_threshold=self.conf_threshold,
            iou_threshold=self.iou_threshold,
        )

    def postprocess(self, detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        # Keep all detections; custom filtering can be added here.
        return detections

    def visualize(self, image_bgr: np.ndarray, detections: List[Dict[str, Any]]) -> np.ndarray:
        return draw_pothole_boxes(image_bgr, detections)

    def run(self, image_bgr: np.ndarray):
        prep = self.preprocess(image_bgr)
        detections = self.infer(prep)
        detections = self.postprocess(detections)
        vis = self.visualize(image_bgr, detections)
        return detections, vis

