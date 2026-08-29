"""Eval DDA run_detection vs GT on drone labeling packs (report-accuracy loop).

Usage:
  python scripts/eval_drone_gt_packs.py --tag baseline
  python scripts/eval_drone_gt_packs.py --tag after_op
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

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

PACKS = [
    "dda_before1_after",
    "dda_before3_after3",
    "dda_before4_after4",
    "dda_before5_after5",
    "dda_before6_after6",
]


def _metrics(pred: np.ndarray, gt: np.ndarray) -> dict:
    p = pred.astype(bool).ravel()
    g = gt.astype(bool).ravel()
    tp = int(np.logical_and(p, g).sum())
    fp = int(np.logical_and(p, ~g).sum())
    fn = int(np.logical_and(~p, g).sum())
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    iou = tp / (tp + fp + fn) if (tp + fp + fn) else 0.0
    return {
        "f1": round(f1, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "iou": round(iou, 4),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "pred_change_pct": round(100.0 * float(p.mean()), 4),
        "gt_change_pct": round(100.0 * float(g.mean()), 4),
    }


def _pack_paths(pair_id: str):
    pack = ROOT / "docs" / "delhi_eval" / "dda_labeling" / pair_id
    before = pack / "before.png"
    after = pack / "after.png"
    gt = ROOT / "docs" / "delhi_eval" / "labels" / f"{pair_id}.png"
    meta = pack / "meta.json"
    # Prefer original TIFs when present next to meta (library-style path)
    tif_b = pack / "before.tif"
    tif_a = pack / "after.tif"
    before_path = str(tif_b) if tif_b.is_file() else str(before)
    after_path = str(tif_a) if tif_a.is_file() else str(after)
    return before, after, gt, meta, before_path, after_path


def eval_one(pair_id: str, enable_registration: bool = True) -> dict:
    from app.detection_engine import run_detection

    before_p, after_p, gt_p, meta_p, before_path, after_path = _pack_paths(pair_id)
    if not before_p.is_file() or not after_p.is_file() or not gt_p.is_file():
        return {"pair_id": pair_id, "error": "missing files"}

    meta = {}
    if meta_p.is_file():
        meta = json.loads(meta_p.read_text(encoding="utf-8"))

    before_pil = Image.open(before_p).convert("RGB")
    after_pil = Image.open(after_p).convert("RGB")
    if after_pil.size != before_pil.size:
        after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)

    t0 = time.time()
    change_mask, _vis, stats, regions = run_detection(
        before_pil,
        after_pil,
        method="AI-Based Deep Learning",
        enable_registration=enable_registration,
        enable_normalization=True,
        detection_sensitivity=0.5,
        min_region_area=150,
        max_size=max(before_pil.size),
        before_path=before_path if before_path.endswith((".tif", ".tiff")) else None,
        after_path=after_path if after_path.endswith((".tif", ".tiff")) else None,
    )
    elapsed = time.time() - t0

    gt = np.array(Image.open(gt_p).convert("L")) > 127
    pred = np.asarray(change_mask)
    if pred.ndim == 3:
        pred = pred[..., 0]
    pred = pred > 127
    if pred.shape != gt.shape:
        gt_img = Image.fromarray((gt.astype(np.uint8) * 255))
        gt_img = gt_img.resize((pred.shape[1], pred.shape[0]), Image.Resampling.NEAREST)
        gt = np.array(gt_img) > 127

    m = _metrics(pred, gt)
    other = sum(
        1 for r in (regions or [])
        if "Other" in str(r.get("change_type") or r.get("type") or "")
        or "other" in str(r.get("change_type") or "").lower()
        or "Unclassified" in str(r.get("change_type") or "")
    )
    return {
        "pair_id": pair_id,
        "ncc_meta": meta.get("ncc"),
        "elapsed_s": round(elapsed, 2),
        "n_regions": len(regions or []),
        "n_other_like": other,
        "report_change_pct": round(float(stats.get("change_percentage") or 0.0), 4),
        "registration": (stats.get("params") or {}).get("registration"),
        "registration_ok": (stats.get("params") or {}).get("registration_ok"),
        "weights": os.environ.get("ADAPTFORMER_WEIGHTS"),
        "thr": os.environ.get("ADAPTFORMER_THRESHOLD") or os.environ.get("DETECTION_DL_THRESHOLD"),
        "tta": os.environ.get("DETECTION_TTA"),
        "skip_reg": os.environ.get("DETECTION_SKIP_REGISTRATION_GEOTIFF"),
        **m,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="eval")
    ap.add_argument("--out-dir", default="data/delhi_cd/friday_drone_report_fix")
    ap.add_argument("--no-register", action="store_true")
    args = ap.parse_args()

    out_dir = ROOT / args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for pid in PACKS:
        print(f"=== {pid} ===", flush=True)
        row = eval_one(pid, enable_registration=not args.no_register)
        rows.append(row)
        if "error" in row:
            print("  ERROR", row["error"], flush=True)
        else:
            print(
                f"  F1={row['f1']:.3f} P={row['precision']:.3f} R={row['recall']:.3f} "
                f"pred%={row['pred_change_pct']:.2f} gt%={row['gt_change_pct']:.2f} "
                f"regions={row['n_regions']}",
                flush=True,
            )

    ok = [r for r in rows if "f1" in r]
    summary = {
        "tag": args.tag,
        "created_unix": time.time(),
        "env": {
            "ADAPTFORMER_WEIGHTS": os.environ.get("ADAPTFORMER_WEIGHTS"),
            "ADAPTFORMER_THRESHOLD": os.environ.get("ADAPTFORMER_THRESHOLD"),
            "DETECTION_DL_THRESHOLD": os.environ.get("DETECTION_DL_THRESHOLD"),
            "DETECTION_TTA": os.environ.get("DETECTION_TTA"),
            "DETECTION_SKIP_REGISTRATION_GEOTIFF": os.environ.get(
                "DETECTION_SKIP_REGISTRATION_GEOTIFF"
            ),
            "DETECTION_FUSION": os.environ.get("DETECTION_FUSION"),
        },
        "mean_f1": round(float(np.mean([r["f1"] for r in ok])), 4) if ok else 0.0,
        "mean_precision": round(float(np.mean([r["precision"] for r in ok])), 4) if ok else 0.0,
        "mean_recall": round(float(np.mean([r["recall"] for r in ok])), 4) if ok else 0.0,
        "pairs": rows,
    }
    out = out_dir / f"{args.tag}.json"
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(
        f"\nSaved {out} | mean_F1={summary['mean_f1']:.4f} "
        f"P={summary['mean_precision']:.3f} R={summary['mean_recall']:.3f}",
        flush=True,
    )


if __name__ == "__main__":
    main()
