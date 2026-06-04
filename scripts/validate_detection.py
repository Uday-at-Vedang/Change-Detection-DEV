"""
Lightweight validation for the change detection pipeline.
Run from change_detection_webapp: python scripts/validate_detection.py
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.detection_engine import (  # noqa: E402
    register_images,
    run_detection,
    fuse_dl_and_classical,
)


def test_registration_identical_pair():
    rng = np.random.default_rng(42)
    img = rng.integers(0, 255, (320, 320, 3), dtype=np.uint8)
    b, a, ok, meta = register_images(img, img.copy())
    assert meta.get("ncc", 0) >= 0.5 or ok, f"weak NCC on identical pair: {meta}"
    print("  registration identical pair:", ok, meta)


def test_registration_with_shift():
    img = np.zeros((400, 400, 3), dtype=np.uint8)
    img[80:200, 80:200] = [180, 90, 60]
    shifted = np.roll(np.roll(img, 8, axis=0), 5, axis=1)
    b, a, ok, meta = register_images(img, shifted)
    print("  registration shifted pair:", ok, "ncc=", meta.get("ncc"))


def test_fusion_shapes():
    h, w = 128, 128
    dl = np.zeros((h, w), dtype=np.float32)
    dl[40:80, 40:80] = 0.8
    cl = np.zeros((h, w), dtype=np.float32)
    cl[50:90, 50:90] = 0.7
    img = np.full((h, w, 3), 128, dtype=np.uint8)
    mask, score, dbg = fuse_dl_and_classical(dl, cl, img, img, sensitivity=0.5)
    assert mask.shape == (h, w)
    assert score.shape == (h, w)
    assert dbg.get("fused_changed_px", 0) >= 0
    print("  fusion:", dbg.get("fused_changed_px"), "px")


def test_run_detection_synthetic():
    rng = np.random.default_rng(0)
    before = rng.integers(0, 255, (256, 256, 3), dtype=np.uint8)
    after = before.copy()
    after[100:180, 100:180] = [40, 180, 40]
    mask, _, stats, regions = run_detection(
        Image.fromarray(before),
        Image.fromarray(after),
        method="AI-Based Deep Learning",
        enable_registration=True,
        enable_normalization=True,
        detection_sensitivity=0.5,
    )
    assert mask.shape[:2] == (256, 256)
    ratio = stats["change_percentage"]
    assert 0 <= ratio <= 100
    assert "params" in stats
    assert not stats.get("threshold_debug", {}).get("fallback_used", False)
    print("  run_detection: change%=", f"{ratio:.2f}", "regions=", len(regions))


def main():
    print("validate_detection.py")
    test_registration_identical_pair()
    test_registration_with_shift()
    test_fusion_shapes()
    test_run_detection_synthetic()
    print("All checks passed.")


if __name__ == "__main__":
    main()
