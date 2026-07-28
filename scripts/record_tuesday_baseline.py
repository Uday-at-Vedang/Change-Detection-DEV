"""Tuesday baseline (Uday P0): held-out F1/P/R with REAL v3_frozen weights.

Evaluates the delhi_cd test split (never used for training calibration of this
checkpoint) via ``app.evaluation.metrics.binary_metrics`` + delhi_cd GT masks.

Usage:
    python scripts/record_tuesday_baseline.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

CKPT = (ROOT / "models" / "adaptformer_delhi" / "v3_frozen").resolve()
os.environ["ADAPTFORMER_WEIGHTS"] = str(CKPT)
# Model-native operating point for Friday apples-to-apples comparison
os.environ["ADAPTFORMER_THRESHOLD"] = "0.2"
os.environ["DETECTION_DL_THRESHOLD"] = "0.2"
os.environ["DETECTION_TTA"] = "off"
os.environ["DETECTION_FUSION"] = "dl_only"

from app.evaluation.metrics import binary_metrics  # noqa: E402
from app.model_inference import (  # noqa: E402
    get_calibrated_threshold,
    get_loaded_weights_source,
    get_model_status,
    predict_change_mask,
    preload_model,
)

OUT = ROOT / "runs" / "tuesday_baseline_20260728"
TEST_IDS = ROOT / "data" / "delhi_cd" / "test" / "pair_ids.txt"
SPLIT_ROOT = ROOT / "data" / "delhi_cd" / "test"


def _load_rgb(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"))


def _load_gt(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        return (np.asarray(im.convert("L")) > 127)


def _pair_paths(pair_id: str) -> tuple[Path, Path, Path]:
    # Prefer split folder layout; fall back to flat delhi_cd tiles if present.
    for base in (SPLIT_ROOT, ROOT / "data" / "delhi_cd"):
        b = base / f"{pair_id}_before.png"
        a = base / f"{pair_id}_after.png"
        g = base / f"{pair_id}_gt.png"
        if not g.is_file():
            g = base / f"{pair_id}_mask.png"
        if b.is_file() and a.is_file() and g.is_file():
            return b, a, g
    # delhi_cd/test/manifest.json → library_sources geotiffs + docs labels
    man_path = SPLIT_ROOT / "manifest.json"
    if man_path.is_file():
        man = json.loads(man_path.read_text(encoding="utf-8"))
        for p in man.get("pairs", []):
            if p.get("pair_id") == pair_id:
                before = ROOT / p["before_path"]
                after = ROOT / p["after_path"]
                gt = ROOT / p["gt_mask"]
                if before.is_file() and after.is_file() and gt.is_file():
                    return before, after, gt
    raise FileNotFoundError(f"Missing assets for {pair_id}")


def _load_pair_arrays(before_p: Path, after_p: Path, gt_p: Path):
    from app.evaluation.delhi_eval import _load_label, _load_rgb as _er
    before = _er(before_p)
    after = _er(after_p)
    gt = _load_label(gt_p)
    return before, after, gt

def main() -> int:
    if not (CKPT / "model.safetensors").is_file():
        print(f"MISSING weights: {CKPT / 'model.safetensors'}")
        print("Run: python scripts/export_adaptformer_delhi.py --src models/adaptformer_delhi/best_v3")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    pair_ids = [ln.strip() for ln in TEST_IDS.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"ckpt={CKPT}")
    print(f"test pairs ({len(pair_ids)}): {pair_ids}")

    ok = preload_model()
    status = get_model_status()
    loaded = get_loaded_weights_source() or status.get("loadedFrom")
    print(f"preload ok={ok} loadedFrom={loaded} thr={get_calibrated_threshold(0.2)}")
    if not ok or (loaded and "v3_frozen" not in str(loaded).replace("\\", "/")):
        print("ERROR: v3_frozen was not loaded — refusing to record baseline")
        return 1

    thr = float(get_calibrated_threshold(0.2) or 0.2)
    rows = []
    t0 = time.time()
    for pid in pair_ids:
        before_p, after_p, gt_p = _pair_paths(pid)
        before, after, gt = _load_pair_arrays(before_p, after_p, gt_p)
        gt = np.asarray(gt) > 127
        pred_mask, _score = predict_change_mask(before, after, threshold=thr)
        pred = np.asarray(pred_mask) > 127
        if pred.shape != gt.shape:
            pred = np.array(
                Image.fromarray((pred.astype(np.uint8) * 255)).resize(
                    (gt.shape[1], gt.shape[0]), Image.NEAREST
                )
            ) > 127
        m = binary_metrics(pred, gt)
        row = {"pair_id": pid, **m, "threshold": thr}
        rows.append(row)
        print(
            f"  {pid}: F1={m['f1']:.4f} P={m['precision']:.4f} "
            f"R={m['recall']:.4f} IoU={m['iou']:.4f}"
        )

    n = max(len(rows), 1)
    summary = {
        "date": datetime.now(timezone.utc).astimezone().isoformat(),
        "role": "Tuesday baseline for Friday comparison (Uday P0)",
        "weights": str(CKPT),
        "loadedFrom": str(loaded),
        "threshold": thr,
        "fusion": "dl_only",
        "tta": "off",
        "split": "data/delhi_cd/test",
        "pair_ids": pair_ids,
        "n_pairs": len(rows),
        "mean_f1": round(sum(r["f1"] for r in rows) / n, 4),
        "mean_precision": round(sum(r["precision"] for r in rows) / n, 4),
        "mean_recall": round(sum(r["recall"] for r in rows) / n, 4),
        "mean_iou": round(sum(r["iou"] for r in rows) / n, 4),
        "elapsed_sec": round(time.time() - t0, 1),
        "model_status": status,
        "per_pair": rows,
    }
    out_path = OUT / "metrics.json"
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print(
        f"BASELINE  F1={summary['mean_f1']}  "
        f"P={summary['mean_precision']}  R={summary['mean_recall']}  "
        f"IoU={summary['mean_iou']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
