"""
Day 7 (Uday): compare fine-tuned AdaptFormer vs pretrained hub weights on
held-out Delhi pairs (docs/delhi_eval/test_split.json) and data/delhi_cd/test.

Usage:
    python scripts/evaluate_finetuned_vs_baseline.py
    python scripts/evaluate_finetuned_vs_baseline.py --methods AI
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.detection_engine import run_detection  # noqa: E402
from app.evaluation.delhi_eval import iter_delhi_pairs  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402


def _load_held_out_ids() -> list[str]:
    path = ROOT / "docs" / "delhi_eval" / "test_split.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        return list(data.get("held_out_test_set", {}).get("pair_ids", []))
    # fallback: data/delhi_cd/test
    man = ROOT / "data" / "delhi_cd" / "test" / "manifest.json"
    if man.is_file():
        return [p["pair_id"] for p in json.loads(man.read_text())["pairs"]]
    return []


def _eval_weights(label: str, weights: str | None, pairs: list, method: str) -> dict:
    # Clear cached model so weights switch takes effect
    import app.model_inference as mi
    mi._MODEL = None
    mi._PROCESSOR = None
    mi._AVAILABLE = None
    mi._LOAD_FAILED = False
    mi._LOAD_ERROR = None
    mi._LOADED_FROM = None

    if weights:
        os.environ["ADAPTFORMER_WEIGHTS"] = weights
    else:
        os.environ.pop("ADAPTFORMER_WEIGHTS", None)
        # Force hub by pointing at a non-existent local so auto-dir is skipped
        # unless user has models/adaptformer_delhi — temporarily rename env.
        os.environ["ADAPTFORMER_WEIGHTS"] = "deepang/adaptformer-LEVIR-CD"

    f1s, ious = [], []
    per = {}
    t0 = time.time()
    for before, after, gt, pair_id, bp, ap in pairs:
        mask, _img, stats, _regions = run_detection(
            Image.fromarray(before), Image.fromarray(after),
            method=method,
            enable_registration=True, enable_normalization=True,
            detection_sensitivity=0.5,
            before_path=bp, after_path=ap,
        )
        if gt is None:
            # empty-GT FP test: IoU=1 if pred empty else 0
            pred_pos = int(np.sum(mask > 127))
            m = {
                "f1": 1.0 if pred_pos == 0 else 0.0,
                "iou": 1.0 if pred_pos == 0 else 0.0,
                "precision": 1.0 if pred_pos == 0 else 0.0,
                "recall": 1.0 if pred_pos == 0 else 0.0,
            }
        else:
            if mask.shape != gt.shape:
                import cv2
                mask = cv2.resize(mask, (gt.shape[1], gt.shape[0]),
                                  interpolation=cv2.INTER_NEAREST)
            m = binary_metrics(mask, gt)
        f1s.append(m["f1"])
        ious.append(m["iou"])
        per[pair_id] = {"f1": m["f1"], "iou": m["iou"],
                        "changePct": round(stats.get("change_percentage", 0), 3)}
        print(f"  [{label}] {pair_id}: F1={m['f1']:.3f} IoU={m['iou']:.3f}")

    return {
        "label": label,
        "weights": weights or "hub:deepang/adaptformer-LEVIR-CD",
        "n": len(pairs),
        "mean_f1": round(float(np.mean(f1s)), 4) if f1s else 0.0,
        "mean_iou": round(float(np.mean(ious)), 4) if ious else 0.0,
        "seconds": round(time.time() - t0, 1),
        "pairs": per,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default="docs/delhi_eval/manifest.json")
    parser.add_argument("--finetuned", default="models/adaptformer_delhi/best")
    parser.add_argument("--method", default="AI-Based Deep Learning")
    parser.add_argument("--out", default="runs/day7_heldout_comparison.json")
    parser.add_argument("--also-feature", action="store_true",
                        help="also report Feature-Based on the same held-out set")
    args = parser.parse_args()

    held_ids = set(_load_held_out_ids())
    if not held_ids:
        raise SystemExit("No held-out pair IDs found in test_split.json")

    all_pairs = list(iter_delhi_pairs(args.manifest, require_gt=False))
    pairs = [p for p in all_pairs if p[3] in held_ids]
    # Prefer labeled if available; empty GT still included for FP test
    if not pairs:
        raise SystemExit(f"No held-out pairs loaded from {args.manifest}")

    print(f"Held-out pairs: {len(pairs)} -> {[p[3] for p in pairs]}")

    ft_path = ROOT / args.finetuned
    rows = []
    rows.append(_eval_weights("pretrained_hub", None, pairs, args.method))
    if ft_path.is_dir():
        rows.append(_eval_weights("finetuned_delhi", str(ft_path), pairs, args.method))
    else:
        print(f"WARNING: {ft_path} missing — run scripts/export_adaptformer_delhi.py first")

    if args.also_feature:
        # Feature-Based doesn't use AdaptFormer weights
        import app.model_inference as mi
        mi._MODEL = None
        f1s, ious, per = [], [], {}
        for before, after, gt, pair_id, bp, ap in pairs:
            mask, _img, stats, _ = run_detection(
                Image.fromarray(before), Image.fromarray(after),
                method="Feature-Based",
                enable_registration=True, enable_normalization=True,
                detection_sensitivity=0.5, before_path=bp, after_path=ap,
            )
            if gt is None:
                pred_pos = int(np.sum(mask > 127))
                m = {"f1": 1.0 if pred_pos == 0 else 0.0,
                     "iou": 1.0 if pred_pos == 0 else 0.0}
            else:
                if mask.shape != gt.shape:
                    import cv2
                    mask = cv2.resize(mask, (gt.shape[1], gt.shape[0]),
                                      interpolation=cv2.INTER_NEAREST)
                m = binary_metrics(mask, gt)
            f1s.append(m["f1"]); ious.append(m["iou"])
            per[pair_id] = m
        rows.append({
            "label": "feature_based",
            "weights": "n/a",
            "n": len(pairs),
            "mean_f1": round(float(np.mean(f1s)), 4),
            "mean_iou": round(float(np.mean(ious)), 4),
            "pairs": per,
        })

    report = {
        "held_out_ids": sorted(held_ids),
        "method": args.method,
        "comparison": rows,
        "winner": max(rows, key=lambda r: r["mean_f1"])["label"] if rows else None,
        "rca_notes": [
            "Domain mismatch (LEVIR→Delhi) is Critical #2 — fine-tune may still underperform "
            "on empty-GT FP pairs; Feature-Based remains strong for precision gates.",
            "GeoTIFF fullres_tiled auto + veg/registration fixes applied from RCA PDF.",
        ],
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n=== Day 7 comparison ===")
    for r in rows:
        print(f"  {r['label']:20s} mean_F1={r['mean_f1']:.4f} mean_IoU={r['mean_iou']:.4f} n={r['n']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
