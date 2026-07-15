"""
Uday Day-2+ calibration sweep: rank methods and DETECTION_* flags on Delhi GT.

Priyanka's ``compare_methods.py`` handles single-pair and manifest batch runs.
This script adds the ranked leaderboard + car-FP regression gate for calibration.

Run from change_detection_webapp:
    python scripts/delhi_calibration_sweep.py --manifest docs/delhi_eval/manifest.json
    python scripts/delhi_calibration_sweep.py --dummy --quick   # scaffold smoke test
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))

from app.detection_engine import run_detection  # noqa: E402
from app.evaluation.delhi_eval import (  # noqa: E402
    DelhiEvalNotReady,
    dummy_delhi_pairs,
    iter_delhi_pairs,
)
from app.evaluation.metrics import binary_metrics  # noqa: E402

try:
    from validate_detection import _case_parked_cars  # noqa: E402
except ImportError:
    def _case_parked_cars(size=384):
        """Synthetic parked-car FP gate when validate_detection has no case."""
        rng = np.random.default_rng(7)
        before = rng.integers(60, 180, (size, size, 3), dtype=np.uint8)
        after = before.copy()
        gt = np.zeros((size, size), dtype=np.uint8)
        for cx, cy in ((90, 120), (200, 160), (280, 220)):
            after[cy:cy + 18, cx:cx + 32] = [40, 40, 45]
            gt[cy:cy + 18, cx:cx + 32] = 255
        return before, after, gt

METHODS = [
    "AI-Based Deep Learning",
    "Feature-Based",
    "KPCA (Unsupervised)",
    "Hybrid AI",
    "Hybrid Approach",
]

DEFAULT_ENV_SWEEPS = [
    {},
    {"DETECTION_FUSION": "hysteresis"},
    {"DETECTION_KPCA": "on"},
    {"DETECTION_KPCA": "off"},
]


@contextmanager
def env_overlay(overrides: dict[str, str]):
    saved: dict[str, str | None] = {}
    for key, value in overrides.items():
        saved[key] = os.environ.get(key)
        os.environ[key] = value
    try:
        yield
    finally:
        for key, old in saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _resize_mask(mask: np.ndarray, gt_shape: tuple[int, int]) -> np.ndarray:
    if mask.shape[:2] == gt_shape:
        return mask
    from cv2 import resize, INTER_NEAREST
    return resize(mask, (gt_shape[1], gt_shape[0]), interpolation=INTER_NEAREST)


def _eval_delhi(method: str, sensitivity: float, env: dict[str, str],
                pairs: list[tuple], before_paths: list[str | None],
                after_paths: list[str | None]) -> dict:
    labeled = [(b, a, g, pid, bp, ap) for b, a, g, pid, bp, ap in zip(
        [p[0] for p in pairs], [p[1] for p in pairs], [p[2] for p in pairs],
        [p[3] for p in pairs], before_paths, after_paths) if g is not None]
    if not labeled:
        return {"mean": {}, "pairs": {}, "n_pairs": 0, "n_labeled": 0}

    agg = {k: [] for k in ("iou", "f1", "precision", "recall", "kappa")}
    per_pair: dict[str, dict] = {}
    with env_overlay(env):
        for before, after, gt, pair_id, bp, ap in labeled:
            mask, _img, stats, regions = run_detection(
                Image.fromarray(before), Image.fromarray(after),
                method=method,
                enable_registration=True,
                enable_normalization=True,
                detection_sensitivity=sensitivity,
                before_path=bp,
                after_path=ap,
            )
            mask = _resize_mask(mask, gt.shape)
            m = binary_metrics(mask, gt)
            per_pair[pair_id] = {
                "metrics": m,
                "regions": len(regions),
                "changePct": round(stats.get("change_percentage", 0), 3),
            }
            for k in agg:
                agg[k].append(m[k])

    mean = {k: round(float(np.mean(v)), 4) for k, v in agg.items()}
    return {"mean": mean, "pairs": per_pair, "n_pairs": len(labeled), "n_labeled": len(labeled)}


def _eval_car_gate(method: str, sensitivity: float, env: dict[str, str]) -> float:
    case = _case_parked_cars()
    before, after, gt = case[:3]
    with env_overlay(env):
        mask, _img, _stats, _regions = run_detection(
            Image.fromarray(before), Image.fromarray(after),
            method=method,
            enable_registration=True,
            enable_normalization=True,
            detection_sensitivity=sensitivity,
        )
    mask = _resize_mask(mask, gt.shape)
    return binary_metrics(mask, gt)["f1"]


def build_configs(quick: bool, methods: list[str] | None = None) -> list[dict]:
    use_methods = methods or METHODS
    sensitivities = [0.5] if quick else [0.35, 0.5, 0.65]
    configs: list[dict] = []
    for method in use_methods:
        for sensitivity in sensitivities:
            for env in (DEFAULT_ENV_SWEEPS[:2] if quick else DEFAULT_ENV_SWEEPS):
                label = method
                if env:
                    label += " | " + ", ".join(f"{k}={v}" for k, v in sorted(env.items()))
                configs.append({
                    "label": label,
                    "method": method,
                    "sensitivity": sensitivity,
                    "env": env,
                })
    return configs


def _load_pairs(manifest: Path | None, dummy: bool):
    if dummy:
        tuples = dummy_delhi_pairs()
    else:
        try:
            tuples = list(iter_delhi_pairs(manifest))
        except DelhiEvalNotReady as exc:
            raise SystemExit(str(exc)) from exc
        if not tuples:
            raise SystemExit("No Delhi pairs with images on disk.")
    pairs = [(b, a, g, pid) for b, a, g, pid, _, _ in tuples]
    bps = [bp for _, _, _, _, bp, _ in tuples]
    aps = [ap for _, _, _, _, _, ap in tuples]
    return pairs, bps, aps


def run_sweep(out_dir: Path, manifest: Path | None, quick: bool, dummy: bool,
              methods: list[str] | None = None) -> list[dict]:
    pairs, bps, aps = _load_pairs(manifest, dummy)
    n_labeled = sum(1 for p in pairs if p[2] is not None)
    print(f"Loaded {len(pairs)} pair(s), {n_labeled} with GT masks.")
    if n_labeled == 0 and not dummy:
        print("No labeled pairs yet — F1 leaderboard will be empty. "
              "Priyanka's masks in docs/delhi_eval/labels/ unlock metrics.")

    configs = build_configs(quick, methods)
    rows: list[dict] = []
    print(f"Delhi calibration sweep ({len(configs)} configs)...")
    for i, cfg in enumerate(configs, 1):
        t0 = time.perf_counter()
        delhi = _eval_delhi(cfg["method"], cfg["sensitivity"], cfg["env"], pairs, bps, aps)
        car_f1 = _eval_car_gate(cfg["method"], cfg["sensitivity"], cfg["env"])
        elapsed = round(time.perf_counter() - t0, 1)
        mean = delhi.get("mean") or {}
        row = {
            "rank": 0,
            "label": cfg["label"],
            "method": cfg["method"],
            "sensitivity": cfg["sensitivity"],
            "env": cfg["env"],
            "delhi_mean_f1": mean.get("f1"),
            "delhi_mean_iou": mean.get("iou"),
            "delhi_mean_kappa": mean.get("kappa"),
            "delhi_n_pairs": delhi.get("n_pairs", 0),
            "car_gate_f1": car_f1,
            "seconds": elapsed,
            "delhi_pairs": delhi.get("pairs", {}),
        }
        rows.append(row)
        f1 = mean.get("f1")
        print(f"  [{i}/{len(configs)}] {cfg['label'][:60]:60s} "
              f"F1={f1 if f1 is not None else 'n/a':>5} car={car_f1:.3f} ({elapsed}s)")

    rows.sort(key=lambda r: (-(r["delhi_mean_f1"] or -1), -(r["car_gate_f1"] or 0)))
    for rank, row in enumerate(rows, 1):
        row["rank"] = rank

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "leaderboard.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    csv_path = out_dir / "leaderboard.csv"
    fields = [
        "rank", "label", "method", "sensitivity", "env",
        "delhi_mean_f1", "delhi_mean_iou", "delhi_mean_kappa",
        "delhi_n_pairs", "car_gate_f1", "seconds",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row[k] for k in fields})

    best = rows[0] if rows else {}
    best_params = {
        "method": best.get("method"),
        "sensitivity": best.get("sensitivity"),
        "env": best.get("env"),
        "delhi_mean_f1": best.get("delhi_mean_f1"),
        "delhi_mean_iou": best.get("delhi_mean_iou"),
        "car_gate_f1": best.get("car_gate_f1"),
        "delhi_n_pairs": best.get("delhi_n_pairs"),
    }
    for name in ("best_config.json", "best_params.json"):
        (out_dir / name).write_text(json.dumps(best_params, indent=2), encoding="utf-8")
    print(f"\nWrote {out_dir / 'leaderboard.json'}, {csv_path}, best_params.json")
    if best:
        print(f"Best: {best['label']} (Delhi F1={best.get('delhi_mean_f1')}, "
              f"car gate={best.get('car_gate_f1')})")
    return rows


def main():
    parser = argparse.ArgumentParser(description="Delhi GT calibration leaderboard sweep")
    parser.add_argument("--manifest", type=str, default="docs/delhi_eval/manifest.json")
    parser.add_argument("--out", type=str, default="runs/calibration")
    parser.add_argument("--methods", type=str, default="",
                        help="comma-separated methods (default: all)")
    parser.add_argument("--dummy", action="store_true")
    parser.add_argument("--quick", action="store_true")
    args = parser.parse_args()

    manifest = Path(args.manifest).resolve() if not args.dummy else None
    methods = [m.strip() for m in args.methods.split(",") if m.strip()] or None
    run_sweep(Path(args.out).resolve(), manifest, args.quick, args.dummy, methods)


if __name__ == "__main__":
    main()
