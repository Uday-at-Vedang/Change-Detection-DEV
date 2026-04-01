from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_yolo_model():
    """
    Lazy-load Ultralytics YOLO model once per process.

    Env:
    - POTHOLE_MODEL_PATH: local path or model name (default: yolov8n.pt)
    """
    model_path = os.environ.get("POTHOLE_MODEL_PATH", "yolov8n.pt").strip() or "yolov8n.pt"
    from ultralytics import YOLO
    return YOLO(model_path)

