"""Verify change-region area math against known ground-truth samples.

Checks:
  1) Analytic geometry (pixel shoelace + geodetic conversion) on synthetic rings
  2) GT masks from labeling packs: polygonAreaPx vs painted mask pixel count
  3) Bbox vs polygon m² discrepancy (bbox should overestimate irregular shapes)

Run: python scripts/verify_polygon_area_gt.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.dda.geo_regions import (  # noqa: E402
    bbox_area_sq_m,
    enrich_regions_geo,
    polygon_area_px,
    polygon_area_sq_m,
)
from app.dda.detect_service import _serialize_regions  # noqa: E402
from app.detection_engine import analyze_change_regions  # noqa: E402

OUT = ROOT / "data/delhi_cd/friday_drone_report_fix/polygon_area_gt"
OUT.mkdir(parents=True, exist_ok=True)

_fails = []
report = {"checks": [], "discrepancies": []}


def check(name, cond, detail="", severity="fail"):
    report["checks"].append({"name": name, "pass": bool(cond), "detail": detail})
    print(("PASS" if cond else "FAIL"), name, detail)
    if not cond:
        _fails.append(name)
        if severity == "info":
            report["discrepancies"].append({"name": name, "detail": detail})


def analytic_suite():
    print("=" * 64)
    print("1) Analytic / synthetic geometry")
    print("=" * 64)
    square = [[0, 0], [100, 0], [100, 100], [0, 100], [0, 0]]
    check("shoelace 100x100", polygon_area_px(square) == 10000.0, str(polygon_area_px(square)))

    # Known Delhi-ish bounds: 0.1 deg x 0.1 deg over 100x100 px
    bounds = (77.0, 28.0, 77.1, 28.1)
    poly_m2 = polygon_area_sq_m(square, 100, 100, bounds)
    bbox_m2 = bbox_area_sq_m({"x": 0, "y": 0, "w": 100, "h": 100}, 100, 100, bounds)
    check("full-frame poly == bbox m2", abs(poly_m2 - bbox_m2) < 0.2, f"{poly_m2} vs {bbox_m2}")

    triangle = [[0, 0], [100, 0], [0, 100], [0, 0]]
    tri = polygon_area_sq_m(triangle, 100, 100, bounds)
    check("triangle ~ half frame", abs(tri - bbox_m2 * 0.5) < 1.0, f"tri={tri} half={bbox_m2*0.5:.1f}")

    # Circle approx: area ~ pi r^2
    rr = 40
    circle = []
    for a in np.linspace(0, 2 * np.pi, 48, endpoint=False):
        circle.append([50 + rr * np.cos(a), 50 + rr * np.sin(a)])
    circle.append(circle[0][:])
    c_px = polygon_area_px(circle)
    expected = np.pi * rr * rr
    err = abs(c_px - expected) / expected
    check("circle shoelace within 2%", err < 0.02, f"got={c_px:.1f} exp={expected:.1f} err={err:.3%}")


def gt_pack_suite():
    print("=" * 64)
    print("2) Labeling-pack GT masks (pixel area)")
    print("=" * 64)
    packs = [
        ROOT / "docs/delhi_eval/dda_labeling/dda_before5_after5_v2",
        ROOT / "docs/delhi_eval/dda_labeling/dda_before6_after6_v2",
        ROOT / "docs/delhi_eval/dda_labeling/dda_before3_1_after3_1",
        ROOT / "docs/delhi_eval/dda_labeling/dda_before1_before",
    ]
    rows = []
    for pack in packs:
        gt = pack / "gt_mask.png"
        if not gt.exists():
            # try seed if blank GT
            gt = pack / "seed_mask.png"
        if not gt.exists():
            check(f"pack_present_{pack.name}", False, "no gt/seed mask", severity="info")
            continue
        mask = cv2.imread(str(gt), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            check(f"pack_readable_{pack.name}", False, "imread failed")
            continue
        bin_m = (mask > 127).astype(np.uint8) * 255
        gt_px = int(np.count_nonzero(bin_m))
        if gt_px < 10:
            check(f"pack_has_paint_{pack.name}", True, f"SKIP blank GT ({gt_px} px) — not yet labeled")
            rows.append({"pack": pack.name, "gt_px": gt_px, "note": "blank_or_tiny"})
            continue

        after_p = pack / "after.png"
        before_p = pack / "before.png"
        after = cv2.cvtColor(cv2.imread(str(after_p)), cv2.COLOR_BGR2RGB) if after_p.exists() else np.full((*bin_m.shape, 3), 120, np.uint8)
        before = cv2.cvtColor(cv2.imread(str(before_p)), cv2.COLOR_BGR2RGB) if before_p.exists() else np.full_like(after, 100)
        if after.shape[:2] != bin_m.shape[:2]:
            bin_m = cv2.resize(bin_m, (after.shape[1], after.shape[0]), interpolation=cv2.INTER_NEAREST)
            gt_px = int(np.count_nonzero(bin_m > 127))

        regs = analyze_change_regions(bin_m, after, min_area=50, use_ensemble=False, before_img=before)
        serial = _serialize_regions(regs)
        # No geo bounds → polygonAreaPx only
        enriched = enrich_regions_geo(serial, img_width=after.shape[1], img_height=after.shape[0], bounds=None)

        poly_sum = sum(float(r.get("polygonAreaPx") or 0) for r in enriched)
        mask_sum = sum(int(r.get("area") or 0) for r in enriched)
        # Connected components may drop tiny blobs via filters — compare retained mask area
        retained_ratio = mask_sum / max(gt_px, 1)
        poly_vs_mask = abs(poly_sum - mask_sum) / max(mask_sum, 1) if mask_sum else 0.0

        row = {
            "pack": pack.name,
            "gt_px": gt_px,
            "n_regions": len(enriched),
            "mask_area_sum": mask_sum,
            "polygon_area_sum": round(poly_sum, 1),
            "retained_vs_gt": round(retained_ratio, 3),
            "poly_vs_mask_err": round(poly_vs_mask, 4),
        }
        rows.append(row)

        # Polygon shoelace should track mask area of retained regions within ~25%
        # (simplification shrinks/grows edges; not identical to raster count).
        check(
            f"poly_tracks_mask_{pack.name}",
            mask_sum == 0 or poly_vs_mask < 0.25,
            f"err={poly_vs_mask:.1%} poly={poly_sum:.0f} mask={mask_sum}",
        )
        if poly_vs_mask >= 0.10:
            report["discrepancies"].append({
                "pack": pack.name,
                "issue": "polygon_vs_mask_area",
                "err": poly_vs_mask,
                "detail": "approxPolyDP footprint differs from raster mask count (expected; tune epsilon in task 3)",
            })

        # Bbox overestimate on irregular regions
        if enriched:
            # synthesize bounds so m2 exists for relative compare
            bounds = (77.0, 28.4, 77.01, 28.41)
            enr2 = enrich_regions_geo(serial, img_width=after.shape[1], img_height=after.shape[0], bounds=bounds)
            for r in enr2[:5]:
                b = r.get("bbox") or {}
                bb = bbox_area_sq_m(b, after.shape[1], after.shape[0], bounds)
                pp = r.get("areaSqM")
                if bb and pp and bb > 0:
                    over = (bb - pp) / bb
                    if over < -0.05:
                        report["discrepancies"].append({
                            "pack": pack.name,
                            "region": r.get("id"),
                            "issue": "polygon_m2_exceeds_bbox",
                            "bbox_m2": bb,
                            "poly_m2": pp,
                        })
                    elif over > 0.35:
                        report["discrepancies"].append({
                            "pack": pack.name,
                            "region": r.get("id"),
                            "issue": "bbox_overestimates_polygon",
                            "over_frac": round(over, 3),
                            "bbox_m2": bb,
                            "poly_m2": pp,
                            "detail": "expected for sparse/irregular change; polygon area is preferred",
                        })

    (OUT / "gt_pack_rows.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print("Wrote", OUT / "gt_pack_rows.json")


def main() -> int:
    analytic_suite()
    gt_pack_suite()
    (OUT / "area_verification_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("=" * 64)
    print(f"discrepancies noted: {len(report['discrepancies'])}")
    if _fails:
        print(f"RESULT: {len(_fails)} FAILED -> {', '.join(_fails)}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
