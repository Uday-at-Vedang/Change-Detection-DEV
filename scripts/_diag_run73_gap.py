"""Ablate before6/after6 using the live GeoTIFF job path (resize + skip-reg NCC)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
load_dotenv(ROOT / ".env", override=True)

from app.detection_engine import (  # noqa: E402
    _alignment_ncc,
    ai_deep_learning_method,
    analyze_change_regions,
    get_detection_max_size,
    normalize_radiometry,
    preprocess_image,
    recover_chromatic_roof_construction,
    recover_dark_roof_construction,
    strip_parking_cluster_from_mask,
    strip_shadow_fragments_from_mask,
    strip_shadow_only_from_mask,
    strip_transient_from_mask,
    strip_weak_seasonal_veg_from_mask,
)


def count(m):
    return int(np.sum(m > 127))


def main():
    before_p = ROOT / "data/library_sources/central_delhi/Images/before6.tif"
    after_p = ROOT / "data/library_sources/central_delhi/Images/after6.tif"
    before_pil = Image.open(before_p).convert("RGB")
    after_pil = Image.open(after_p).convert("RGB")
    # Match job_runner: resize after onto before
    if after_pil.size != before_pil.size:
        after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)

    ms = get_detection_max_size()
    b = preprocess_image(before_pil, max_size=ms)
    a = preprocess_image(after_pil, max_size=ms)
    ncc = float(_alignment_ncc(b, a))
    registration_ok = bool(ncc >= 0.55)
    print("shape", b.shape, "ncc", round(ncc, 4), "registration_ok", registration_ok)
    print("weights", os.environ.get("ADAPTFORMER_WEIGHTS"), "thr", os.environ.get("ADAPTFORMER_THRESHOLD"))

    b_chr, a_chr = b.copy(), a.copy()
    b, a = normalize_radiometry(b, a)

    change_mask, debug = ai_deep_learning_method(
        b, a, sensitivity=0.5, registration_ok=registration_ok
    )
    print("dl_debug", {k: debug.get(k) for k in (
        "threshold_score", "model_changed_px", "combined_changed_px", "method", "model"
    ) if isinstance(debug, dict)})

    steps = [("raw_dl", change_mask.copy())]
    change_mask = strip_transient_from_mask(change_mask, b, a)
    steps.append(("strip_transient", change_mask.copy()))
    change_mask = strip_shadow_only_from_mask(change_mask, b, a)
    steps.append(("strip_shadow_only", change_mask.copy()))
    change_mask = strip_shadow_fragments_from_mask(
        change_mask, b, a, registration_ok=registration_ok
    )
    steps.append(("strip_shadow_fragments_soft", change_mask.copy()))
    change_mask = strip_parking_cluster_from_mask(change_mask, b, a)
    steps.append(("strip_parking", change_mask.copy()))
    change_mask = strip_weak_seasonal_veg_from_mask(change_mask, b, a)
    steps.append(("strip_weak_veg", change_mask.copy()))
    change_mask = recover_chromatic_roof_construction(change_mask, b_chr, a_chr)
    steps.append(("recover_chromatic", change_mask.copy()))
    change_mask = recover_dark_roof_construction(change_mask, b_chr, a_chr)
    steps.append(("recover_dark_roof", change_mask.copy()))
    change_mask = strip_shadow_only_from_mask(change_mask, b, a)
    steps.append(("re_strip_shadow_only", change_mask.copy()))
    change_mask = strip_shadow_fragments_from_mask(
        change_mask, b, a, registration_ok=registration_ok
    )
    steps.append(("re_strip_shadow_fragments_soft", change_mask.copy()))

    out = ROOT / "data/delhi_cd/friday_drone_report_fix/run73_ablation"
    out.mkdir(parents=True, exist_ok=True)
    prev = None
    rows = []
    for name, m in steps:
        px = count(m)
        delta = None if prev is None else px - prev
        rows.append({"step": name, "px": px, "delta": delta, "pct": round(100 * px / m.size, 3)})
        print(f"{name:32s} px={px:7d} delta={str(delta):>8s} pct={100*px/m.size:.3f}%")
        cv2.imwrite(str(out / f"{name}.png"), m)
        prev = px

    regs = analyze_change_regions(
        change_mask, a_chr, min_area=150, use_ensemble=False, before_img=b_chr,
        registration_ok=registration_ok,
    )
    print("final_regions", len(regs), "top_areas", [r["area"] for r in regs[:10]])

    # Candidate fixes: skip shadow_only when weak alignment (keep soft fragments)
    m2 = steps[0][1].copy()
    m2 = strip_transient_from_mask(m2, b, a)
    m2 = strip_shadow_fragments_from_mask(m2, b, a, registration_ok=False)
    m2 = strip_parking_cluster_from_mask(m2, b, a)
    m2 = strip_weak_seasonal_veg_from_mask(m2, b, a)
    m2 = recover_chromatic_roof_construction(m2, b_chr, a_chr)
    m2 = recover_dark_roof_construction(m2, b_chr, a_chr)
    # only soft fragment re-strip, no shadow_only
    m2 = strip_shadow_fragments_from_mask(m2, b, a, registration_ok=False)
    print("ALT_no_shadow_only px", count(m2), f"pct={100*count(m2)/m2.size:.3f}%")
    regs2 = analyze_change_regions(
        m2, a_chr, min_area=150, use_ensemble=False, before_img=b_chr,
        registration_ok=False,
    )
    print("ALT_no_shadow_only regions", len(regs2), "top", [r["area"] for r in regs2[:8]])

    # skip ALL shadow strips
    m3 = steps[0][1].copy()
    m3 = strip_transient_from_mask(m3, b, a)
    m3 = strip_parking_cluster_from_mask(m3, b, a)
    m3 = strip_weak_seasonal_veg_from_mask(m3, b, a)
    m3 = recover_chromatic_roof_construction(m3, b_chr, a_chr)
    m3 = recover_dark_roof_construction(m3, b_chr, a_chr)
    print("ALT_no_shadow_at_all px", count(m3), f"pct={100*count(m3)/m3.size:.3f}%")
    regs3 = analyze_change_regions(
        m3, a_chr, min_area=150, use_ensemble=False, before_img=b_chr,
        registration_ok=False,
    )
    print("ALT_no_shadow_at_all regions", len(regs3), "top", [r["area"] for r in regs3[:8]])

    (out / "ablation.json").write_text(json.dumps({"ncc": ncc, "steps": rows}, indent=2), encoding="utf-8")

    # Compare to run61 overlay / mask footprint if we can load overlay difference
    print("run61 target ~193927 px (11.5%), run73 ~123141 (7.3%)")


if __name__ == "__main__":
    main()
