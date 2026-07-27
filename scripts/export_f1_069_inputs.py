"""Export AdaptFormer probability maps for the v3 F1≈0.69 val sweep.

The F1=0.69 figure comes from ``runs/v3_baseline_analysis/analysis.json``
(val sweep @ thr=0.25 → F1=0.6900; best @ thr=0.35 → F1=0.6917).

Writes:
  runs/f1_069_inputs/prob_maps/<pair_id>_prob.png
  runs/f1_069_inputs/gt_masks/<pair_id>.png  (copies)
  runs/f1_069_inputs/summary.json
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "runs" / "f1_069_inputs"
CKPT = ROOT / "models" / "adaptformer_delhi" / "v3_frozen"


def main() -> int:
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    from app.model_inference import predict_change_mask

    man = json.loads((ROOT / "data/delhi_cd/val/manifest.json").read_text(encoding="utf-8"))
    pairs = man["pairs"]
    prob_dir = OUT / "prob_maps"
    gt_dir = OUT / "gt_masks"
    prob_dir.mkdir(parents=True, exist_ok=True)
    gt_dir.mkdir(parents=True, exist_ok=True)

    # Exact row from analysis.json where val_f1 == 0.69
    metrics_at_025 = {
        "threshold": 0.25,
        "val_f1": 0.69,
        "val_precision": 0.7021,
        "val_recall": 0.712,
        "val_iou": 0.5287,
        "source": "runs/v3_baseline_analysis/analysis.json → val_sweep",
    }
    best = {
        "threshold": 0.35,
        "val_f1": 0.6917,
        "val_precision": 0.7324,
        "val_recall": 0.6857,
        "val_iou": 0.5317,
        "source": "runs/v3_baseline_analysis/recommended_threshold.json",
    }

    exported = []
    for row in pairs:
        pid = row["pair_id"]
        before = _load_rgb(ROOT / row["before_path"])
        after = _load_rgb(ROOT / row["after_path"])
        gt_src = ROOT / row["gt_mask"]
        gt = _load_label(gt_src)
        _mask, score = predict_change_mask(before, after, threshold=0.25)
        if score is None:
            print(f"FAIL {pid}: no score")
            continue
        if score.shape[:2] != gt.shape[:2]:
            score = np.array(
                Image.fromarray((score * 255).astype(np.uint8)).resize(
                    (gt.shape[1], gt.shape[0]), Image.BILINEAR
                )
            ).astype(np.float32) / 255.0
        prob_u8 = np.clip(score * 255.0, 0, 255).astype(np.uint8)
        prob_path = prob_dir / f"{pid}_prob.png"
        Image.fromarray(prob_u8).save(prob_path)
        # also save a thumbnail like DETECTION_SAVE_PROB_MAP does
        thumb = Image.fromarray(prob_u8)
        thumb.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        thumb.save(prob_dir / f"{pid}_prob_thumb.png")
        gt_dst = gt_dir / f"{pid}.png"
        shutil.copy2(gt_src, gt_dst)
        exported.append(
            {
                "pair_id": pid,
                "prob_map": str(prob_path.relative_to(ROOT)),
                "gt_mask": str(gt_dst.relative_to(ROOT)),
                "gt_source": row["gt_mask"],
                "score_mean": round(float(score.mean()), 4),
                "score_p99": round(float(np.percentile(score, 99)), 4),
            }
        )
        print(f"OK {pid} -> {prob_path.name}")

    summary = {
        "model": str(CKPT.relative_to(ROOT)),
        "f1_069_operating_point": metrics_at_025,
        "best_val_nearby": best,
        "precision": metrics_at_025["val_precision"],
        "recall": metrics_at_025["val_recall"],
        "f1": metrics_at_025["val_f1"],
        "val_pairs": exported,
        "gt_split": "data/delhi_cd/val (4 pairs)",
        "gt_label_dir": "docs/delhi_eval/labels/",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print("Wrote", OUT / "summary.json")
    print(
        f"Precision={metrics_at_025['val_precision']}  "
        f"Recall={metrics_at_025['val_recall']}  F1={metrics_at_025['val_f1']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
