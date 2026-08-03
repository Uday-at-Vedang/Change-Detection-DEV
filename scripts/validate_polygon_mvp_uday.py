"""Uday 1-day polygon MVP validation: synthetic + real pair checks."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.detection_engine import (  # noqa: E402
    _MAX_POLYGON_VERTICES,
    _bbox_as_polygon,
    _extract_region_polygon,
    analyze_change_regions,
    visualize_changes,
)
from app.dda.detect_service import _serialize_regions  # noqa: E402
from app.dda.geo_regions import enrich_regions_geo  # noqa: E402


def main() -> int:
    out_dir = ROOT / "data/delhi_cd/friday_drone_report_fix/polygon_mvp_uday"
    out_dir.mkdir(parents=True, exist_ok=True)
    report: dict = {"checks": [], "ok": True}

    def check(name: str, cond: bool, detail: str = "") -> None:
        report["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
        if not cond:
            report["ok"] = False
        print(("PASS" if cond else "FAIL"), name, detail)

    # --- bbox fallback ---
    fb = _bbox_as_polygon(10, 20, 30, 40)
    check("bbox_fallback_closed", fb[0] == fb[-1] and len(fb) == 5, str(fb))

    # --- Synthetic shapes ---
    h, w = 256, 256
    labels = np.zeros((h, w), np.int32)
    cv2.circle(labels, (80, 80), 35, 1, -1)
    labels[150:220, 40:70] = 2
    labels[190:220, 40:160] = 2

    mask = (labels > 0).astype(np.uint8) * 255
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = (120, 110, 100)
    img[labels == 1] = (40, 90, 200)
    img[labels == 2] = (180, 160, 140)
    before = np.full_like(img, 110)

    t0 = time.perf_counter()
    poly1 = _extract_region_polygon(labels, 1, (45, 45, 70, 70))
    poly2 = _extract_region_polygon(labels, 2, (40, 150, 120, 70))
    t_extract = (time.perf_counter() - t0) * 1000

    check(
        "synthetic_circle_verts",
        3 <= len(poly1) <= _MAX_POLYGON_VERTICES + 1,
        f"n={len(poly1)} closed={poly1[0] == poly1[-1]}",
    )
    check(
        "synthetic_L_verts",
        3 <= len(poly2) <= _MAX_POLYGON_VERTICES + 1,
        f"n={len(poly2)} closed={poly2[0] == poly2[-1]}",
    )

    empty = _extract_region_polygon(np.zeros((50, 50), np.int32), 9, (5, 5, 10, 10))
    check("empty_fallback_bbox", empty == _bbox_as_polygon(5, 5, 10, 10), str(empty))

    noisy = np.zeros((200, 200), np.int32)
    for ang in np.linspace(0, 2 * np.pi, 80, endpoint=False):
        r = 55 + 12 * np.sin(7 * ang)
        cx, cy = int(100 + r * np.cos(ang)), int(100 + r * np.sin(ang))
        cv2.circle(noisy, (cx, cy), 8, 1, -1)
    cv2.circle(noisy, (100, 100), 40, 1, -1)
    poly_jag = _extract_region_polygon(noisy, 1, (20, 20, 160, 160))
    n_unique = len(poly_jag) - (1 if poly_jag[0] == poly_jag[-1] else 0)
    check(
        "jagged_capped_60",
        n_unique <= _MAX_POLYGON_VERTICES,
        f"unique={n_unique} total={len(poly_jag)}",
    )

    regs_a = analyze_change_regions(
        mask, img, min_area=200, use_ensemble=False, before_img=before
    )
    regs_b = analyze_change_regions(
        mask, img, min_area=200, use_ensemble=False, before_img=before
    )

    def fingerprint(rs):
        return [
            (
                r["id"],
                r["area"],
                tuple(r["bbox"]),
                r["object_type"],
                round(float(r["confidence"]), 4),
            )
            for r in rs
        ]

    check("deterministic_count", len(regs_a) == len(regs_b), f"{len(regs_a)} vs {len(regs_b)}")
    check("fingerprint_stable", fingerprint(regs_a) == fingerprint(regs_b), "")
    check(
        "all_have_polygon",
        all(r.get("polygon") and len(r["polygon"]) >= 4 for r in regs_a),
        f"n_regions={len(regs_a)}",
    )
    check("no_holes_key", all("polygon_holes" not in r for r in regs_a), "")

    over = False
    for r in regs_a:
        n_u = len(r["polygon"]) - (1 if r["polygon"][0] == r["polygon"][-1] else 0)
        if n_u > _MAX_POLYGON_VERTICES:
            check(f"region_{r['id']}_cap", False, str(n_u))
            over = True
            break
    if not over:
        check("all_regions_capped", True, f"count={len(regs_a)}")

    t0 = time.perf_counter()
    viz = visualize_changes(before, img, mask, regions=regs_a)
    t_viz = (time.perf_counter() - t0) * 1000
    check("viz_shape", viz.shape == img.shape, str(viz.shape))
    check("viz_time_sane_ms", t_viz < 500, f"{t_viz:.1f}ms")
    cv2.imwrite(
        str(out_dir / "synthetic_overlay.png"),
        cv2.cvtColor(viz, cv2.COLOR_RGB2BGR),
    )

    serial = _serialize_regions(regs_a)
    check("serialize_has_polygon", all("polygon" in s for s in serial), "")
    bare = _serialize_regions(
        [
            {
                "id": 1,
                "area": 10,
                "center": (1, 1),
                "bbox": (0, 0, 5, 5),
                "object_type": "Other",
                "confidence": 0.5,
                "severity": "minor",
            }
        ]
    )[0]
    check("serialize_absent_when_missing", "polygon" not in bare, "")

    bounds = (77.0, 28.0, 77.1, 28.1)
    enriched = enrich_regions_geo(serial, img_width=w, img_height=h, bounds=bounds)
    check(
        "polygonGeo_present",
        all("polygonGeo" in e for e in enriched if e.get("polygon")),
        f"n={len(enriched)}",
    )
    if enriched and enriched[0].get("polygonGeo"):
        g0 = enriched[0]["polygonGeo"][0]
        check("polygonGeo_keys", "lat" in g0 and "lng" in g0, str(g0))

    pack = ROOT / "docs/delhi_eval/dda_labeling/dda_before5_after5_v2"
    before_p = pack / "before.png"
    after_p = pack / "after.png"
    gt_p = pack / "gt_mask.png"
    if before_p.exists() and after_p.exists() and gt_p.exists():
        b = cv2.cvtColor(cv2.imread(str(before_p)), cv2.COLOR_BGR2RGB)
        a = cv2.cvtColor(cv2.imread(str(after_p)), cv2.COLOR_BGR2RGB)
        gt = cv2.imread(str(gt_p), cv2.IMREAD_GRAYSCALE)
        if gt.shape[:2] != a.shape[:2]:
            gt = cv2.resize(gt, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_NEAREST)
        gt_bin = (gt > 127).astype(np.uint8) * 255
        t0 = time.perf_counter()
        real_regs = analyze_change_regions(
            gt_bin, a, min_area=100, use_ensemble=False, before_img=b
        )
        t_an = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        real_viz = visualize_changes(b, a, gt_bin, regions=real_regs)
        t_rv = (time.perf_counter() - t0) * 1000
        cv2.imwrite(
            str(out_dir / "real_dda_before5_overlay.png"),
            cv2.cvtColor(real_viz, cv2.COLOR_RGB2BGR),
        )
        check(
            "real_regions_have_poly",
            all(r.get("polygon") for r in real_regs) if real_regs else True,
            f"n={len(real_regs)}",
        )
        check("real_viz_time_sane", t_rv < 2000, f"{t_rv:.1f}ms analyze={t_an:.1f}ms")
        r2 = analyze_change_regions(
            gt_bin, a, min_area=100, use_ensemble=False, before_img=b
        )
        check(
            "real_count_stable",
            len(real_regs) == len(r2) and fingerprint(real_regs) == fingerprint(r2),
            f"n={len(real_regs)}",
        )
        serial_r = _serialize_regions(real_regs)
        (out_dir / "real_regions_sample.json").write_text(
            json.dumps(serial_r[:5], indent=2), encoding="utf-8"
        )
        report["real_analyze_ms"] = round(t_an, 2)
        report["real_viz_ms"] = round(t_rv, 2)
        report["real_region_count"] = len(real_regs)
    else:
        check("real_pair_found", False, "dda_before5 pack missing")

    report["extract_ms_synthetic"] = round(t_extract, 2)
    report["viz_ms_synthetic"] = round(t_viz, 2)
    (out_dir / "validation_report.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    print("\n=== SUMMARY ok=", report["ok"], "===")
    print("Wrote", out_dir)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
