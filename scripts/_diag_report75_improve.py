"""Ablate report-75 improvements: ribbon strip + soft DL close on before6/after6."""
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
    _is_alignment_edge_ribbon,
    ai_deep_learning_method,
    analyze_change_regions,
    get_detection_max_size,
    normalize_radiometry,
    preprocess_image,
    recover_chromatic_roof_construction,
    recover_dark_roof_construction,
    strip_alignment_edge_ribbons_from_mask,
    split_weakly_bridged_change_blobs,
    strip_parking_cluster_from_mask,
    strip_shadow_fragments_from_mask,
    strip_shadow_only_from_mask,
    strip_transient_from_mask,
    strip_weak_seasonal_veg_from_mask,
    visualize_changes,
)


def main():
    before_p = ROOT / "data/library_sources/central_delhi/Images/before6.tif"
    after_p = ROOT / "data/library_sources/central_delhi/Images/after6.tif"
    b_pil = Image.open(before_p).convert("RGB")
    a_pil = Image.open(after_p).convert("RGB")
    if a_pil.size != b_pil.size:
        a_pil = a_pil.resize(b_pil.size, Image.Resampling.LANCZOS)
    ms = get_detection_max_size()
    b0 = preprocess_image(b_pil, max_size=ms)
    a0 = preprocess_image(a_pil, max_size=ms)
    ncc = float(_alignment_ncc(b0, a0))
    registration_ok = ncc >= 0.55
    print("ncc", round(ncc, 4), "registration_ok", registration_ok, "ms", ms)

    b_chr, a_chr = b0.copy(), a0.copy()
    b, a = normalize_radiometry(b0, a0)

    # Match UI job: sensitivity 0.45
    change_mask, debug = ai_deep_learning_method(
        b, a, sensitivity=0.45, registration_ok=registration_ok
    )
    print("dl", {k: debug.get(k) for k in (
        "threshold_score", "model_changed_px", "combined_changed_px")})

    def count(m):
        return int(np.sum(m > 127))

    steps = [("raw_clean+chroma", change_mask.copy())]
    change_mask = strip_transient_from_mask(change_mask, b, a)
    steps.append(("transient", change_mask.copy()))
    change_mask = strip_shadow_only_from_mask(
        change_mask, b, a, registration_ok=registration_ok)
    steps.append(("shadow_only", change_mask.copy()))
    change_mask = strip_shadow_fragments_from_mask(
        change_mask, b, a, registration_ok=registration_ok)
    steps.append(("fragments", change_mask.copy()))
    change_mask = strip_alignment_edge_ribbons_from_mask(change_mask)
    steps.append(("ribbons", change_mask.copy()))
    change_mask = strip_parking_cluster_from_mask(change_mask, b, a)
    change_mask = strip_weak_seasonal_veg_from_mask(change_mask, b, a)
    change_mask = recover_chromatic_roof_construction(change_mask, b_chr, a_chr)
    change_mask = recover_dark_roof_construction(
        change_mask, b_chr, a_chr, registration_ok=registration_ok)
    steps.append(("recover", change_mask.copy()))
    change_mask = strip_shadow_fragments_from_mask(
        change_mask, b, a, registration_ok=False)
    change_mask = strip_alignment_edge_ribbons_from_mask(change_mask)
    change_mask = split_weakly_bridged_change_blobs(change_mask)
    steps.append(("final", change_mask.copy()))

    prev = None
    for name, m in steps:
        px = count(m)
        delta = None if prev is None else px - prev
        print(f"{name:18s} px={px:7d} delta={delta} pct={100*px/m.size:.2f}%")
        prev = px

    regs = analyze_change_regions(
        change_mask, a_chr, min_area=150, use_ensemble=False,
        before_img=b_chr, registration_ok=False,
    )
    print("regions", len(regs), "top", [
        (r["id"], r["area"], r["bbox"][2], r["bbox"][3],
         round(max(r["bbox"][2], r["bbox"][3]) / max(min(r["bbox"][2], r["bbox"][3]), 1), 1),
         _is_alignment_edge_ribbon(r["area"], r["bbox"][2], r["bbox"][3], r.get("fill_ratio")))
        for r in regs[:15]
    ])

    out = ROOT / "data/delhi_cd/friday_drone_report_fix/report75_improve"
    out.mkdir(parents=True, exist_ok=True)
    overlay = visualize_changes(
        b_chr, a_chr, change_mask, regions=regs, shape_mode="polygon")
    cv2.imwrite(str(out / "overlay_bgr.png"), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    print("wrote", out / "overlay_bgr.png")


if __name__ == "__main__":
    main()
