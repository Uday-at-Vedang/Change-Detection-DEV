"""Balanced weak-align path for before6/after6 (post report 78 under-detect)."""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
from dotenv import load_dotenv
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env", override=True)

from app.detection_engine import (  # noqa: E402
    _alignment_ncc,
    ai_deep_learning_method,
    analyze_change_regions,
    normalize_radiometry,
    preprocess_image,
    recover_chromatic_roof_construction,
    recover_dark_roof_construction,
    split_weakly_bridged_change_blobs,
    strip_alignment_edge_ribbons_from_mask,
    strip_parking_cluster_from_mask,
    strip_shadow_fragments_from_mask,
    strip_shadow_only_from_mask,
    strip_transient_from_mask,
    strip_weak_seasonal_veg_from_mask,
    visualize_changes,
)


def main():
    b_pil = Image.open(
        ROOT / "data/library_sources/central_delhi/Images/before6.tif").convert("RGB")
    a_pil = Image.open(
        ROOT / "data/library_sources/central_delhi/Images/after6.tif").convert("RGB")
    if a_pil.size != b_pil.size:
        a_pil = a_pil.resize(b_pil.size, Image.Resampling.LANCZOS)
    b0 = preprocess_image(b_pil, max_size=20000)
    a0 = preprocess_image(a_pil, max_size=20000)
    ncc = float(_alignment_ncc(b0, a0))
    ok = ncc >= 0.55
    b_chr, a_chr = b0.copy(), a0.copy()
    b, a = normalize_radiometry(b0, a0)
    m, dbg = ai_deep_learning_method(b, a, sensitivity=0.45, registration_ok=ok)
    print("thr", dbg.get("threshold_score"), "combined", dbg.get("combined_changed_px"))

    def px(x):
        return int(np.sum(x > 127))

    m = strip_transient_from_mask(m, b, a, registration_ok=ok)
    m = strip_shadow_only_from_mask(m, b, a, registration_ok=ok)
    m = strip_shadow_fragments_from_mask(m, b, a, registration_ok=ok)
    m = strip_alignment_edge_ribbons_from_mask(m)
    m = strip_parking_cluster_from_mask(m, b, a)
    m = strip_weak_seasonal_veg_from_mask(m, b, a)
    print("pre_recover", px(m))
    m = recover_chromatic_roof_construction(m, b_chr, a_chr, registration_ok=ok)
    print("chroma", px(m))
    m = recover_dark_roof_construction(m, b_chr, a_chr, registration_ok=ok)
    print("dark", px(m))
    m = strip_shadow_fragments_from_mask(m, b, a, registration_ok=False)
    m = strip_alignment_edge_ribbons_from_mask(m)
    m = split_weakly_bridged_change_blobs(m)
    print("final", px(m), f"{100*px(m)/m.size:.2f}%")
    regs = analyze_change_regions(
        m, a_chr, min_area=150, use_ensemble=False,
        before_img=b_chr, registration_ok=False)
    print("regions", len(regs), "top", [r["area"] for r in regs[:8]])
    out = ROOT / "data/delhi_cd/friday_drone_report_fix/report78_rebalance"
    out.mkdir(parents=True, exist_ok=True)
    ov = visualize_changes(b_chr, a_chr, m, regions=regs, shape_mode="polygon")
    cv2.imwrite(str(out / "overlay.png"), cv2.cvtColor(ov, cv2.COLOR_RGB2BGR))
    print("target: between 78=55k/10 and 77=151k/24; prefer ~100-130k with majors")


if __name__ == "__main__":
    main()
