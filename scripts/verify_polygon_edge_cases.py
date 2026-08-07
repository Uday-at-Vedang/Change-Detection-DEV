"""Edge-case checks: overlap NMS, nested fragments, tiny noisy polygons."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.detection_engine import (  # noqa: E402
    _bbox_as_polygon,
    _nms_regions,
    _stabilize_tiny_region_polygons,
    analyze_change_regions,
)


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    return bool(cond)


def main() -> int:
    ok = True

    # Overlapping bboxes → NMS keeps larger
    big = {
        "id": 1, "area": 1000, "bbox": (10, 10, 80, 80),
        "polygon": _bbox_as_polygon(10, 10, 80, 80),
    }
    small = {
        "id": 2, "area": 200, "bbox": (30, 30, 40, 40),
        "polygon": _bbox_as_polygon(30, 30, 40, 40),
    }
    kept = _nms_regions([big, small], iou_thresh=0.3)
    ok &= check("nms_drops_overlap", len(kept) == 1 and kept[0]["area"] == 1000, str(len(kept)))

    # Nested fragment with low bbox IoU but polygon inside parent
    parent = {
        "id": 1, "area": 5000, "bbox": (0, 0, 100, 100),
        "polygon": _bbox_as_polygon(0, 0, 100, 100),
    }
    frag = {
        "id": 2, "area": 100, "bbox": (40, 40, 15, 15),
        "polygon": _bbox_as_polygon(42, 42, 10, 10),
    }
    # bbox IoU of these is low; containment should still drop frag
    kept2 = _nms_regions([parent, frag], iou_thresh=0.45)
    ok &= check("nested_fragment_suppressed", len(kept2) == 1 and kept2[0]["id"] == 1, str(kept2))

    # Tiny noisy polygon stabilized to bbox
    noisy = {
        "id": 1, "area": 80, "bbox": (10, 10, 12, 12),
        "polygon": [[10 + (i % 5), 10 + (i // 5)] for i in range(20)] + [[10, 10]],
    }
    stab = _stabilize_tiny_region_polygons([noisy], min_area=100)
    ok &= check(
        "tiny_noisy_becomes_bbox",
        stab[0]["polygon"] == _bbox_as_polygon(10, 10, 12, 12),
        f"n={len(stab[0]['polygon'])}",
    )

    # Fragmented mask: large blob + tiny island nearby → island may be filtered by min_area
    H, W = 200, 200
    mask = np.zeros((H, W), np.uint8)
    cv2.rectangle(mask, (30, 30), (120, 110), 255, -1)
    cv2.rectangle(mask, (140, 140), (148, 148), 255, -1)  # tiny 8x8
    img = np.full((H, W, 3), 110, np.uint8)
    img[mask > 0] = (50, 100, 200)
    before = np.full_like(img, 100)
    regs = analyze_change_regions(mask, img, min_area=200, use_ensemble=False, before_img=before)
    ids_areas = [(r["id"], r["area"]) for r in regs]
    ok &= check("tiny_island_not_listed_or_stable", all(a >= 150 for _, a in ids_areas), str(ids_areas))

    print("RESULT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
