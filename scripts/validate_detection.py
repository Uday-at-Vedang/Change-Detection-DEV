"""
Lightweight validation + accuracy benchmark for the change detection pipeline.

Run from change_detection_webapp:
    python scripts/validate_detection.py            # unit checks
    python scripts/validate_detection.py --benchmark # synthetic IoU/Dice/F1 report
    python scripts/validate_detection.py --benchmark --out runs/eval  # + comparison PNGs

Benchmarks run on synthetic pairs with known ground-truth change masks, so they
work even without labeled imagery (per project decision: synthetic + review
labels for now). The same harness is used to A/B preprocessing and fusion
toggles by setting the relevant DETECTION_* env vars before running.
"""
import argparse
import json
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
from app.evaluation.metrics import binary_metrics  # noqa: E402


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


# ---------------------------------------------------------------------------
# Synthetic benchmark suite (known ground-truth masks)
# ---------------------------------------------------------------------------

def _base_scene(size=384, seed=0):
    """A textured pseudo-aerial scene so registration/feature matching has signal."""
    rng = np.random.default_rng(seed)
    img = rng.integers(40, 200, (size, size, 3), dtype=np.uint8)
    img = np.array(Image.fromarray(img).resize((size, size)))
    # A few stable structures (roads / fields) common to both timestamps
    img[:, size // 3: size // 3 + 6] = [90, 90, 90]
    img[size // 2: size // 2 + 6, :] = [110, 100, 80]
    return img


def _case_inserted_buildings(size=384):
    before = _base_scene(size, seed=1)
    after = before.copy()
    gt = np.zeros((size, size), dtype=np.uint8)
    boxes = [(60, 70, 50, 40), (220, 90, 60, 55), (150, 250, 70, 45)]
    for (x, y, w, h) in boxes:
        after[y:y + h, x:x + w] = [205, 200, 190]
        gt[y:y + h, x:x + w] = 255
    return before, after, gt, "inserted_buildings"


def _case_brightness_only(size=384):
    # Global illumination change with NO structural change -> GT empty, expect low FP.
    before = _base_scene(size, seed=2)
    after = np.clip(before.astype(np.float32) * 1.18 + 12, 0, 255).astype(np.uint8)
    gt = np.zeros((size, size), dtype=np.uint8)
    return before, after, gt, "brightness_only"


def _case_misaligned_change(size=384):
    before = _base_scene(size, seed=3)
    shifted = np.roll(np.roll(before, 6, axis=0), 4, axis=1)
    after = shifted.copy()
    gt = np.zeros((size, size), dtype=np.uint8)
    x, y, w, h = 180, 160, 80, 60
    after[y:y + h, x:x + w] = [210, 60, 60]
    gt[y:y + h, x:x + w] = 255
    return before, after, gt, "misaligned_change"


def _save_comparison(out_dir: Path, name: str, before, after, pred_mask, gt):
    out_dir.mkdir(parents=True, exist_ok=True)
    h, w = gt.shape
    pred = (pred_mask > 127).astype(np.uint8) * 255
    overlay = after.copy()
    overlay[pred > 0] = (0.45 * overlay[pred > 0] + 0.55 * np.array([255, 40, 40])).astype(np.uint8)
    diff = np.zeros((h, w, 3), dtype=np.uint8)
    diff[(pred > 0) & (gt > 0)] = [0, 200, 0]      # true positive
    diff[(pred > 0) & (gt == 0)] = [255, 0, 0]     # false positive
    diff[(pred == 0) & (gt > 0)] = [0, 0, 255]     # false negative
    panels = [
        before, after,
        np.dstack([gt] * 3), overlay, diff,
    ]
    strip = np.concatenate([np.asarray(p, dtype=np.uint8) for p in panels], axis=1)
    Image.fromarray(strip).save(out_dir / f"{name}.png")


def benchmark_synthetic(out_dir: Path | None = None, sensitivity=0.5):
    cases = [_case_inserted_buildings(), _case_brightness_only(), _case_misaligned_change()]
    report = {}
    print("\nSynthetic benchmark (IoU / Dice / F1 / Precision / Recall):")
    for before, after, gt, name in cases:
        mask, _img, stats, regions = run_detection(
            Image.fromarray(before), Image.fromarray(after),
            method="AI-Based Deep Learning",
            enable_registration=True, enable_normalization=True,
            detection_sensitivity=sensitivity,
        )
        if mask.shape != gt.shape:
            from cv2 import resize, INTER_NEAREST
            mask = resize(mask, (gt.shape[1], gt.shape[0]), interpolation=INTER_NEAREST)
        m = binary_metrics(mask, gt)
        report[name] = {"metrics": m, "regions": len(regions),
                        "changePct": round(stats["change_percentage"], 3)}
        print(f"  {name:20s} IoU={m['iou']:.3f} Dice={m['dice']:.3f} "
              f"F1={m['f1']:.3f} P={m['precision']:.3f} R={m['recall']:.3f} "
              f"FPR={m['falsePositiveRate']:.3f}")
        if out_dir is not None:
            _save_comparison(out_dir, name, before, after, mask, gt)

    if out_dir is not None:
        (out_dir / "metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\n  Wrote comparison images + metrics.json to {out_dir}")
    return report


def main():
    parser = argparse.ArgumentParser(description="Validate + benchmark change detection")
    parser.add_argument("--benchmark", action="store_true", help="run synthetic accuracy benchmark")
    parser.add_argument("--out", type=str, default="", help="dir for comparison images + metrics.json")
    parser.add_argument("--sensitivity", type=float, default=0.5)
    args = parser.parse_args()

    print("validate_detection.py")
    test_registration_identical_pair()
    test_registration_with_shift()
    test_fusion_shapes()
    test_run_detection_synthetic()
    print("All checks passed.")

    if args.benchmark:
        out_dir = Path(args.out).resolve() if args.out else None
        benchmark_synthetic(out_dir=out_dir, sensitivity=args.sensitivity)


if __name__ == "__main__":
    main()
