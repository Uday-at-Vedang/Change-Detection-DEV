"""
Find the accuracy-optimal detection threshold by measurement, not guesswork.

Loads a saved probability map (grayscale PNG where pixel value = model change
probability) and a ground-truth binary mask, then sweeps the threshold and
reports Precision / Recall / F1 / IoU at each level. Answers the question
"is a low F1 caused by false positives (precision) or missed detections
(recall), and which threshold maximises F1?" — cheaply, on CPU, no model.

Why this exists: an F1 number alone is ambiguous. "Lower the threshold" only
helps if RECALL is the problem; if PRECISION is the problem, lowering the
threshold makes F1 worse. This shows precision AND recall at every threshold
so the direction is chosen from evidence.

Get the probability map by running detection once with DETECTION_SAVE_PROB_MAP=true
(saves data/overlays/<run>_prob.png). Pair it with the ground-truth mask.

Usage:
    python scripts/sweep_threshold.py --prob <run>_prob.png --gt gt_mask.png
    python scripts/sweep_threshold.py --prob p.png --gt gt.png --start 0.05 --stop 0.95 --step 0.05
"""
import argparse
from pathlib import Path

import numpy as np
from PIL import Image


def _load_gray(path: str) -> np.ndarray:
    return np.array(Image.open(path).convert("L"))


def _metrics(pred_bool: np.ndarray, gt_bool: np.ndarray) -> dict:
    tp = int(np.sum(pred_bool & gt_bool))
    fp = int(np.sum(pred_bool & ~gt_bool))
    fn = int(np.sum(~pred_bool & gt_bool))
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    iou = tp / (tp + fp + fn) if (tp + fp + fn) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1, "iou": iou,
            "tp": tp, "fp": fp, "fn": fn}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--prob", required=True, help="grayscale probability-map PNG (0-255 = 0..1 prob)")
    parser.add_argument("--gt", required=True, help="binary ground-truth mask PNG (white = changed)")
    parser.add_argument("--start", type=float, default=0.05)
    parser.add_argument("--stop", type=float, default=0.95)
    parser.add_argument("--step", type=float, default=0.05)
    args = parser.parse_args()

    prob = _load_gray(args.prob).astype(np.float32) / 255.0
    gt = _load_gray(args.gt)
    gt_bool = gt > 127

    # Resize prob to GT shape if they differ (nearest keeps the score structure).
    if prob.shape != gt_bool.shape:
        import cv2
        prob = cv2.resize(prob, (gt_bool.shape[1], gt_bool.shape[0]), interpolation=cv2.INTER_LINEAR)

    gt_frac = float(np.mean(gt_bool)) * 100
    print(f"prob map: {prob.shape}  range {prob.min():.2f}-{prob.max():.2f}")
    print(f"ground truth: {gt_bool.shape}  changed pixels = {gt_frac:.2f}% of image\n")

    print(f"{'thresh':>7} {'precision':>10} {'recall':>8} {'F1':>7} {'IoU':>7}   {'TP':>8} {'FP':>8} {'FN':>8}")
    rows = []
    t = args.start
    while t <= args.stop + 1e-9:
        m = _metrics(prob >= t, gt_bool)
        rows.append((t, m))
        print(f"{t:7.2f} {m['precision']:10.3f} {m['recall']:8.3f} {m['f1']:7.3f} {m['iou']:7.3f}   "
              f"{m['tp']:8d} {m['fp']:8d} {m['fn']:8d}")
        t += args.step

    best_t, best_m = max(rows, key=lambda r: r[1]["f1"])
    print(f"\nBest F1 = {best_m['f1']:.3f} at threshold {best_t:.2f} "
          f"(precision={best_m['precision']:.3f}, recall={best_m['recall']:.3f})")

    # Direction hint — evidence, not guess.
    if best_m["precision"] < best_m["recall"] - 0.1:
        print("Diagnosis: PRECISION-limited (too many false positives). "
              "Raising the threshold / filtering helps; LOWERING would hurt.")
    elif best_m["recall"] < best_m["precision"] - 0.1:
        print("Diagnosis: RECALL-limited (missing real changes). "
              "Lowering the threshold (or higher-resolution input) helps.")
    else:
        print("Diagnosis: precision and recall are balanced at the best threshold.")


if __name__ == "__main__":
    main()
