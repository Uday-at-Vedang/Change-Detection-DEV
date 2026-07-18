"""
Ablate production detection configs against the frozen v3 Delhi test split.

Compares offline v3 operating point (DL-only @ thr=0.2) with TTA / registration
/ fusion variants so we can see what the webapp actually delivers vs the
reported Test F1 = 0.581.

Usage (from repo root):
    python scripts/ablate_v3_app_pipeline.py
    python scripts/ablate_v3_app_pipeline.py --configs dl_only_no_tta,full_pipeline
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

# Force v3 frozen weights + calibrated threshold for this benchmark.
os.environ["ADAPTFORMER_WEIGHTS"] = str(
    (ROOT / "models" / "adaptformer_delhi" / "v3_frozen").resolve()
)
os.environ.setdefault("ADAPTFORMER_THRESHOLD", "0.2")
os.environ.setdefault("DETECTION_DL_THRESHOLD", "0.2")
os.environ.setdefault("DETECTION_DL_FLOOR_BASE", "0.15")

from app.detection_engine import run_detection  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402
from app.model_inference import (  # noqa: E402
    get_calibrated_threshold,
    get_loaded_weights_source,
    predict_change_mask,
    preload_model,
)


CONFIGS = {
    "dl_only_no_tta": {
        "desc": "v3 DL-only @ 0.2, TTA off (offline-style)",
        "env": {"DETECTION_TTA": "off", "DETECTION_FUSION": "dl_only"},
        "registration": False,
        "use_engine": False,
    },
    "dl_only_tta": {
        "desc": "v3 DL-only @ 0.2 + CPU TTA (hflip)",
        "env": {"DETECTION_TTA": "hflip", "DETECTION_FUSION": "dl_only"},
        "registration": False,
        "use_engine": False,
    },
    "dl_only_reg": {
        "desc": "v3 DL-only @ 0.2 + registration, TTA off",
        "env": {"DETECTION_TTA": "off", "DETECTION_FUSION": "dl_only"},
        "registration": True,
        "use_engine": True,
    },
    "smart_union": {
        "desc": "v3 + smart_union (floor<=0.15), TTA off, no reg",
        "env": {
            "DETECTION_TTA": "off",
            "DETECTION_FUSION": "smart_union",
            "DETECTION_DL_FLOOR_BASE": "0.15",
        },
        "registration": False,
        "use_engine": True,
    },
    "hysteresis": {
        "desc": "v3 + hysteresis fusion, TTA off, no reg",
        "env": {"DETECTION_TTA": "off", "DETECTION_FUSION": "hysteresis"},
        "registration": False,
        "use_engine": True,
    },
    "full_pipeline": {
        "desc": "Full production-like: smart_union + TTA auto + registration",
        "env": {
            "DETECTION_TTA": "auto",
            "DETECTION_FUSION": "smart_union",
            "DETECTION_DL_FLOOR_BASE": "0.15",
        },
        "registration": True,
        "use_engine": True,
    },
}


def _load_rgb(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".tif", ".tiff"):
        from app.dda.geotiff_io import load_rgb_pil
        return np.array(load_rgb_pil(path))
    return np.array(Image.open(path).convert("RGB"))


def _load_test_pairs(manifest_path: Path):
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    pairs = []
    for row in data.get("pairs", []):
        before = ROOT / row["before_path"]
        after = ROOT / row["after_path"]
        gt_path = ROOT / row["gt_mask"]
        if not (before.is_file() and after.is_file() and gt_path.is_file()):
            print(f"  skip missing files for {row['pair_id']}")
            continue
        before_arr = _load_rgb(before)
        after_arr = _load_rgb(after)
        gt = np.array(Image.open(gt_path).convert("L"))
        pairs.append((row["pair_id"], before_arr, after_arr, gt))
    return pairs


def _apply_env(overrides: dict[str, str]):
    saved = {}
    for key, value in overrides.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value
    return saved


def _restore_env(saved: dict):
    for key, old in saved.items():
        if old is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = old


def _eval_config(name: str, cfg: dict, pairs: list) -> dict:
    saved = _apply_env(cfg["env"])
    # TTA / fusion are read at call time; model already loaded.
    f1s, ious, precs, recs = [], [], [], []
    per_pair = {}
    t0 = time.time()
    thr = get_calibrated_threshold(0.2)
    try:
        for pair_id, before, after, gt in pairs:
            if cfg["use_engine"]:
                mask, _img, _stats, _regions = run_detection(
                    Image.fromarray(before),
                    Image.fromarray(after),
                    method="AI-Based Deep Learning",
                    enable_registration=cfg["registration"],
                    enable_normalization=True,
                    detection_sensitivity=0.5,
                )
            else:
                mask, _score = predict_change_mask(before, after, threshold=thr)
            if mask.shape != gt.shape:
                import cv2
                mask = cv2.resize(
                    mask, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_NEAREST
                )
            m = binary_metrics(mask, gt)
            f1s.append(m["f1"])
            ious.append(m["iou"])
            precs.append(m["precision"])
            recs.append(m["recall"])
            per_pair[pair_id] = m
    finally:
        _restore_env(saved)

    return {
        "config": name,
        "desc": cfg["desc"],
        "n_pairs": len(pairs),
        "threshold": thr,
        "mean_f1": round(float(np.mean(f1s)) if f1s else 0.0, 4),
        "mean_iou": round(float(np.mean(ious)) if ious else 0.0, 4),
        "mean_precision": round(float(np.mean(precs)) if precs else 0.0, 4),
        "mean_recall": round(float(np.mean(recs)) if recs else 0.0, 4),
        "seconds": round(time.time() - t0, 1),
        "per_pair": per_pair,
        "weights": get_loaded_weights_source(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        default="data/delhi_cd/test/manifest.json",
        help="Held-out v3 test split manifest",
    )
    parser.add_argument(
        "--configs",
        default=",".join(CONFIGS.keys()),
        help="Comma-separated config keys",
    )
    parser.add_argument(
        "--out",
        default="runs/v3_app_ablation/results.json",
    )
    args = parser.parse_args()

    wanted = [c.strip() for c in args.configs.split(",") if c.strip()]
    unknown = [c for c in wanted if c not in CONFIGS]
    if unknown:
        raise SystemExit(f"Unknown configs: {unknown}. Choose from {list(CONFIGS)}")

    print("Preloading v3 AdaptFormer weights...")
    ok = preload_model()
    print(f"  loadedFrom={get_loaded_weights_source()} ok={ok} thr={get_calibrated_threshold(0.2)}")
    if not ok or not get_loaded_weights_source() or "v3" not in str(get_loaded_weights_source()).lower():
        raise SystemExit("Refusing to ablate: v3_frozen weights were not loaded")

    pairs = _load_test_pairs(ROOT / args.manifest)
    if not pairs:
        raise SystemExit(f"No usable test pairs in {args.manifest}")
    print(f"Evaluating {len(pairs)} test pair(s): {[p[0] for p in pairs]}\n")

    rows = []
    for name in wanted:
        print(f"=== {name}: {CONFIGS[name]['desc']}")
        row = _eval_config(name, CONFIGS[name], pairs)
        rows.append(row)
        print(
            f"  F1={row['mean_f1']:.4f}  P={row['mean_precision']:.4f}  "
            f"R={row['mean_recall']:.4f}  IoU={row['mean_iou']:.4f}  "
            f"({row['seconds']}s)"
        )

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "baseline_offline_v3": {
            "test_f1": 0.5809,
            "precision": 0.6782,
            "recall": 0.5351,
            "iou": 0.4117,
            "threshold": 0.2,
        },
        "rows": rows,
    }
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    best = max(rows, key=lambda r: r["mean_f1"])
    print(f"Best app config: {best['config']} mean_f1={best['mean_f1']}")


if __name__ == "__main__":
    main()
