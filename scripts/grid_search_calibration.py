"""
Day 4: grid-search detection parameters against the labeled Delhi set and
rank configs by mean IoU/F1, so calibration is based on measurement instead
of guesswork.

Sweeps (via env vars the engine already reads, see app/detection_config.py):
    - detection_sensitivity      (CLI --sensitivities)
    - DETECTION_FUSION           smart_union | hysteresis
    - DETECTION_DL_FLOOR_BASE    DL confidence floor for smart_union fusion
    - DETECTION_CL_Q_BASE        classical-score percentile floor

Runs full-factorial over whatever lists you pass; keep lists short for
DL/Hybrid methods (each pair costs ~45-60s) — that's why this defaults to
Feature-Based (cheap, ~3s/pair) unless --methods overrides it.

Usage:
    # cheap sweep across sensitivity x fusion on the classical path
    python scripts/grid_search_calibration.py --methods "Feature-Based" \\
        --sensitivities 0.2,0.3,0.4,0.5,0.6,0.7,0.8 --fusions smart_union,hysteresis

    # targeted DL-floor probe on a few pairs before committing to the full set
    python scripts/grid_search_calibration.py --methods "AI-Based Deep Learning" \\
        --sensitivities 0.5 --dl-floors 0.10,0.15,0.20,0.25,0.30,0.36 \\
        --pair-ids delhi_0001,delhi_0005,delhi_0009

    # full sweep once a promising region is known
    python scripts/grid_search_calibration.py --methods "AI-Based Deep Learning" \\
        --sensitivities 0.4,0.5,0.6 --dl-floors 0.15,0.20 --cl-qs 0.85,0.90
"""
import argparse
import csv
import json
import os
import sys
import time
from itertools import product
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

from app.detection_engine import run_detection  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402


def _load_rgb(path: Path):
    if path.suffix.lower() in (".tif", ".tiff"):
        import rasterio
        with rasterio.open(path) as ds:
            return np.transpose(ds.read([1, 2, 3]), (1, 2, 0))
    return np.array(Image.open(path).convert("RGB"))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default="docs/delhi_eval/manifest.json")
    parser.add_argument("--pair-ids", default="", help="comma list to restrict to a subset (for fast probing)")
    parser.add_argument("--methods", default="Feature-Based")
    parser.add_argument("--sensitivities", default="0.5")
    parser.add_argument("--fusions", default="smart_union", help="comma list: smart_union,hysteresis")
    parser.add_argument("--dl-floors", default="", help="comma list for DETECTION_DL_FLOOR_BASE (blank = engine default 0.36)")
    parser.add_argument("--cl-qs", default="", help="comma list for DETECTION_CL_Q_BASE (blank = engine default 0.92)")
    parser.add_argument("--out", default="runs/calibration/leaderboard.csv")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    pairs = [p for p in manifest["pairs"] if p.get("gt_mask")]
    if args.pair_ids:
        wanted = set(args.pair_ids.split(","))
        pairs = [p for p in pairs if p["pair_id"] in wanted]
    if not pairs:
        raise SystemExit("No labeled pairs matched — nothing to grid-search.")

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    sensitivities = [float(s) for s in args.sensitivities.split(",") if s.strip()]
    fusions = [f.strip() for f in args.fusions.split(",") if f.strip()]
    dl_floors = [f.strip() for f in args.dl_floors.split(",") if f.strip()] or [None]
    cl_qs = [f.strip() for f in args.cl_qs.split(",") if f.strip()] or [None]

    # Preload images once — reused across every config in the sweep.
    loaded = []
    for pair in pairs:
        before = _load_rgb(ROOT / pair["before_path"])
        after = _load_rgb(ROOT / pair["after_path"])
        gt = np.array(Image.open(ROOT / pair["gt_mask"]).convert("L"))
        loaded.append((pair["pair_id"], before, after, gt))

    configs = list(product(methods, sensitivities, fusions, dl_floors, cl_qs))
    print(f"{len(configs)} config(s) x {len(loaded)} pair(s) = {len(configs) * len(loaded)} detection run(s)\n")

    out_path = ROOT / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = []

    for method, sensitivity, fusion, dl_floor, cl_q in configs:
        os.environ["DETECTION_FUSION"] = fusion
        if dl_floor is not None:
            os.environ["DETECTION_DL_FLOOR_BASE"] = dl_floor
        else:
            os.environ.pop("DETECTION_DL_FLOOR_BASE", None)
        if cl_q is not None:
            os.environ["DETECTION_CL_Q_BASE"] = cl_q
        else:
            os.environ.pop("DETECTION_CL_Q_BASE", None)

        ious, f1s = [], []
        t0 = time.time()
        for pair_id, before, after, gt in loaded:
            mask, _img, stats, _regions = run_detection(
                Image.fromarray(before), Image.fromarray(after),
                method=method, enable_registration=True, enable_normalization=True,
                detection_sensitivity=sensitivity,
            )
            if mask.shape != gt.shape:
                import cv2
                mask = cv2.resize(mask, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_NEAREST)
            m = binary_metrics(mask, gt)
            ious.append(m["iou"])
            f1s.append(m["f1"])
        elapsed = time.time() - t0

        row = {
            "method": method, "sensitivity": sensitivity, "fusion": fusion,
            "dl_floor_base": dl_floor or "default(0.36)", "cl_q_base": cl_q or "default(0.92)",
            "mean_iou": round(float(np.mean(ious)), 4), "mean_f1": round(float(np.mean(f1s)), 4),
            "n_pairs": len(loaded), "nonzero_iou": sum(1 for x in ious if x > 0),
            "seconds": round(elapsed, 1),
        }
        rows.append(row)
        print(f"  {row}")

    rows.sort(key=lambda r: -r["mean_f1"])
    with open(out_path, "a" if out_path.exists() else "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if f.tell() == 0:
            writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} row(s) to {out_path}")
    print(f"Best this run: {rows[0]}")


if __name__ == "__main__":
    main()
