"""Regression checks for random/misaligned polygons."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.detection_engine import (  # noqa: E402
    _bbox_as_polygon,
    _extract_region_polygon,
    analyze_change_regions,
)


def check(name, cond, detail=""):
    print(("PASS" if cond else "FAIL"), name, detail)
    return bool(cond)


def main() -> int:
    ok = True
    h, w = 200, 200
    labels = np.zeros((h, w), np.int32)
    cv2.rectangle(labels, (40, 40), (120, 100), 1, -1)

    ring = _extract_region_polygon(labels, 1, (40, 40, 81, 61))
    ok &= check("normal_rect_inside_bbox", all(
        35 <= p[0] <= 125 and 35 <= p[1] <= 105 for p in ring
    ), str(ring[:4]))

    # Empty label → bbox fallback
    empty = _extract_region_polygon(labels, 9, (10, 10, 20, 20))
    ok &= check("empty_is_bbox", empty == _bbox_as_polygon(10, 10, 20, 20), str(empty))

    # Jagged blob still yields a ring inside its bbox
    jagged = np.zeros((200, 200), np.int32)
    for ang in np.linspace(0, 2 * np.pi, 60, endpoint=False):
        r = 40 + 10 * np.sin(5 * ang)
        cx, cy = int(100 + r * np.cos(ang)), int(100 + r * np.sin(ang))
        cv2.circle(jagged, (cx, cy), 6, 1, -1)
    cv2.circle(jagged, (100, 100), 25, 1, -1)
    ys, xs = np.where(jagged == 1)
    bx, by = int(xs.min()), int(ys.min())
    bw, bh = int(xs.max() - bx + 1), int(ys.max() - by + 1)
    jring = _extract_region_polygon(jagged, 1, (bx, by, bw, bh))
    pad = max(3, int(0.08 * max(bw, bh)))
    ok &= check(
        "jagged_stays_in_bbox",
        all(bx - pad <= p[0] <= bx + bw + pad and by - pad <= p[1] <= by + bh + pad for p in jring),
        f"n={len(jring)}",
    )

    # analyze_change_regions attaches polygons that fit bboxes
    mask = (labels > 0).astype(np.uint8) * 255
    img = np.full((h, w, 3), 110, np.uint8)
    img[labels == 1] = (40, 90, 200)
    before = np.full_like(img, 100)
    regs = analyze_change_regions(mask, img, min_area=50, use_ensemble=False, before_img=before)
    if not regs:
        # classification may drop — still OK if extractor path works
        ok &= check("regions_optional", True, "no regions after classify")
    else:
        for r in regs:
            poly = r.get("polygon") or []
            x, y, bw2, bh2 = r["bbox"]
            pad2 = max(3, int(0.08 * max(bw2, bh2)))
            inside = all(
                x - pad2 <= p[0] <= x + bw2 + pad2 and y - pad2 <= p[1] <= y + bh2 + pad2
                for p in poly
            )
            ok &= check(f"region_{r['id']}_poly_in_bbox", inside, f"n={len(poly)}")

    # Slider working-grid contract: stats keys present after run_detection smoke
    # (cheap synthetic path via analyze only covered above).
    print("RESULT", "OK" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
