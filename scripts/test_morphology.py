"""
Test whether morphological post-processing of the probability map raises F1,
WITHOUT retraining the model. CPU-only, operates on saved probability maps +
ground-truth masks.

Why: the v3 fine-tuned model detects change in roughly the right places but as
scattered speckle, while the ground truth is solid blobs. Threshold sweeps are
exhausted (precision 0.70 ~ recall 0.71, F1 flat ~0.69). Morphological closing
merges nearby speckle into solid regions and small-blob removal drops isolated
noise — either can move F1 without touching the model.

For each pair it evaluates:
  raw            threshold only (baseline)
  close          threshold -> morphological CLOSE (fill gaps, merge speckle)
  close+open     close then OPEN (also remove tiny isolated noise)
  min_area       threshold -> drop connected components below --min-area px
and reports mean Precision / Recall / F1 / IoU across all pairs, so you can see
which post-process (if any) beats the raw baseline.

Inputs: a directory holding matched <id>_prob.png and <id>.png (GT) files, OR
explicit --prob-dir / --gt-dir. Matches the layout that
export_f1_069_inputs.py writes:
    runs/f1_069_inputs/prob_maps/<id>_prob.png
    runs/f1_069_inputs/gt_masks/<id>.png

Usage:
    python scripts/test_morphology.py --root runs/f1_069_inputs --threshold 0.25
    python scripts/test_morphology.py --prob-dir P --gt-dir G --threshold 0.25 \\
        --kernels 3,5,7 --min-areas 20,50,100
"""
import argparse
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _load_gray(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("L"))


def _metrics(pred_bool: np.ndarray, gt_bool: np.ndarray) -> dict:
    tp = int(np.sum(pred_bool & gt_bool))
    fp = int(np.sum(pred_bool & ~gt_bool))
    fn = int(np.sum(~pred_bool & gt_bool))
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    iou = tp / (tp + fp + fn) if (tp + fp + fn) else 0.0
    return {"precision": p, "recall": r, "f1": f1, "iou": iou}


def _close(mask, k):
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.morphologyEx(mask, cv2.MORPH_CLOSE, ker)


def _open(mask, k):
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, ker)


def _drop_small(mask, min_area):
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[lab == i] = 255
    return out


def _find_pairs(prob_dir: Path, gt_dir: Path):
    pairs = []
    for prob_path in sorted(prob_dir.glob("*_prob.png")):
        pid = prob_path.name[: -len("_prob.png")]
        gt_path = gt_dir / f"{pid}.png"
        if gt_path.exists():
            pairs.append((pid, prob_path, gt_path))
    return pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default="", help="dir with prob_maps/ and gt_masks/ subdirs")
    parser.add_argument("--prob-dir", default="")
    parser.add_argument("--gt-dir", default="")
    parser.add_argument("--threshold", type=float, default=0.25)
    parser.add_argument("--kernels", default="3,5,7", help="morphology kernel sizes to try")
    parser.add_argument("--min-areas", default="20,50,100", help="min connected-component areas to try")
    args = parser.parse_args()

    if args.root:
        prob_dir = Path(args.root) / "prob_maps"
        gt_dir = Path(args.root) / "gt_masks"
    else:
        prob_dir, gt_dir = Path(args.prob_dir), Path(args.gt_dir)
    if not prob_dir.is_dir() or not gt_dir.is_dir():
        raise SystemExit(f"Need prob dir ({prob_dir}) and gt dir ({gt_dir}).")

    pairs = _find_pairs(prob_dir, gt_dir)
    if not pairs:
        raise SystemExit(f"No matched <id>_prob.png / <id>.png pairs in {prob_dir} and {gt_dir}")

    kernels = [int(x) for x in args.kernels.split(",") if x.strip()]
    min_areas = [int(x) for x in args.min_areas.split(",") if x.strip()]
    thr = args.threshold

    # Preload thresholded masks + GT (resized to match).
    data = []
    for pid, pp, gp in pairs:
        prob = _load_gray(pp).astype(np.float32) / 255.0
        gt = _load_gray(gp) > 127
        if prob.shape != gt.shape:
            prob = cv2.resize(prob, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_LINEAR)
        base = ((prob >= thr) * 255).astype(np.uint8)
        data.append((pid, base, gt))

    def _mean(variant_fn) -> dict:
        acc = {"precision": 0.0, "recall": 0.0, "f1": 0.0, "iou": 0.0}
        for _pid, base, gt in data:
            m = _metrics(variant_fn(base) > 127, gt)
            for k in acc:
                acc[k] += m[k]
        return {k: v / len(data) for k, v in acc.items()}

    print(f"{len(data)} pair(s) | threshold={thr}\n")
    print(f"{'variant':28} {'precision':>9} {'recall':>7} {'F1':>7} {'IoU':>7}")

    results = []
    raw = _mean(lambda m: m)
    results.append(("raw (threshold only)", raw))
    print(f"{'raw (threshold only)':28} {raw['precision']:9.3f} {raw['recall']:7.3f} {raw['f1']:7.3f} {raw['iou']:7.3f}")

    for k in kernels:
        r = _mean(lambda m, k=k: _close(m, k))
        results.append((f"close k={k}", r))
        print(f"{'close k='+str(k):28} {r['precision']:9.3f} {r['recall']:7.3f} {r['f1']:7.3f} {r['iou']:7.3f}")
    for k in kernels:
        r = _mean(lambda m, k=k: _open(_close(m, k), 3))
        results.append((f"close k={k} + open 3", r))
        print(f"{'close k='+str(k)+' + open 3':28} {r['precision']:9.3f} {r['recall']:7.3f} {r['f1']:7.3f} {r['iou']:7.3f}")
    for a in min_areas:
        r = _mean(lambda m, a=a: _drop_small(m, a))
        results.append((f"min_area={a}", r))
        print(f"{'min_area='+str(a):28} {r['precision']:9.3f} {r['recall']:7.3f} {r['f1']:7.3f} {r['iou']:7.3f}")
    # combined best-guess: close then drop small
    for k in kernels:
        for a in min_areas:
            r = _mean(lambda m, k=k, a=a: _drop_small(_close(m, k), a))
            results.append((f"close k={k}+min_area={a}", r))
            print(f"{'close k='+str(k)+'+min_area='+str(a):28} {r['precision']:9.3f} {r['recall']:7.3f} {r['f1']:7.3f} {r['iou']:7.3f}")

    best_name, best = max(results, key=lambda kv: kv[1]["f1"])
    print(f"\nBaseline (raw) F1 = {raw['f1']:.3f}")
    print(f"Best variant: {best_name}  F1 = {best['f1']:.3f}  (delta {best['f1']-raw['f1']:+.3f})")
    if best["f1"] - raw["f1"] < 0.005:
        print("=> Morphology does NOT meaningfully help. The ceiling is the model/data, "
              "not post-processing — escalate to more training data / better labels.")
    else:
        print(f"=> Morphology helps: apply '{best_name}' as a post-process step. "
              "Report this as a no-retrain accuracy gain.")


if __name__ == "__main__":
    main()
