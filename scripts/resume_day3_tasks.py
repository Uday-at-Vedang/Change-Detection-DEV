"""
Resume Day 2-3 tasks SEQUENTIALLY (one heavy job at a time).

Previous crashes were likely caused by running 4 AdaptFormer/TensorFlow jobs in
parallel while also writing thousands of mask PNGs to disk.

Usage:
    python scripts/resume_day3_tasks.py
    python scripts/resume_day3_tasks.py --from grid
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable


def run_step(name: str, cmd: list[str]) -> None:
    print(f"\n{'=' * 60}\nSTEP: {name}\n{'=' * 60}")
    subprocess.run(cmd, check=True, cwd=ROOT)
    print(f"STEP DONE: {name}")


def main():
    parser = argparse.ArgumentParser(description="Resume Day 3 tasks sequentially")
    parser.add_argument("--from", dest="from_step",
                        choices=["baseline", "grid", "calibration", "finetune"],
                        default="baseline")
    parser.add_argument("--finetune-epochs", type=int, default=12)
    parser.add_argument("--force", action="store_true",
                        help="re-run steps even if output artifacts already exist")
    args = parser.parse_args()

    baseline_out = ROOT / "runs/delhi_baseline/metrics.json"
    grid_out = ROOT / "runs/calibration/best_params.json"
    calib_out = ROOT / "runs/calibration/leaderboard.json"
    finetune_glob = ROOT / "runs/finetune_adaptformer"

    steps: list[tuple[str, list[str], Path | None]] = [
        ("baseline", [PY, "scripts/record_delhi_baseline.py"], baseline_out),
        ("grid", [
            PY, "scripts/grid_search_calibration.py",
            "--manifest", "docs/delhi_eval/manifest.json",
            "--methods", "Feature-Based",
            "--sensitivities", "0.2,0.3,0.4,0.5,0.6,0.7,0.8",
            "--fusions", "smart_union,hysteresis",
            "--out", "runs/calibration/leaderboard.csv",
        ], grid_out),
        ("calibration", [
            PY, "scripts/delhi_calibration_sweep.py",
            "--manifest", "docs/delhi_eval/manifest.json",
            "--out", "runs/calibration",
            "--methods", "Feature-Based",
            "--quick",
        ], calib_out),
        ("finetune", [
            PY, "scripts/finetune_adaptformer.py",
            "--manifest", "docs/delhi_eval/manifest.json",
            "--epochs", str(args.finetune_epochs),
            "--batch-size", "2",
        ], None),
    ]

    start = False
    for name, cmd, artifact in steps:
        if name == args.from_step:
            start = True
        if not start:
            continue
        if artifact and artifact.is_file() and not args.force:
            print(f"\nSKIP {name}: {artifact} already exists (use --force to re-run)")
            continue
        if name == "finetune" and not args.force:
            existing = sorted(finetune_glob.glob("*/metrics.json"))
            if existing:
                print(f"\nSKIP finetune: {existing[-1]} already exists (use --force to re-run)")
                continue
        run_step(name, cmd)

    summary = {}
    for path, key in [
        (baseline_out, "baseline"),
        (grid_out, "calibration_best"),
        (calib_out, "calibration_leaderboard"),
        (ROOT / "runs/calibration/grid_search/manifest_report.json", "grid_search_manifest"),
    ]:
        if path.is_file():
            summary[key] = json.loads(path.read_text(encoding="utf-8"))
    finetune_runs = sorted(finetune_glob.glob("*/metrics.json"))
    if finetune_runs:
        summary["finetune"] = json.loads(finetune_runs[-1].read_text(encoding="utf-8"))

    out = ROOT / "runs/day3_completion_summary.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nAll steps finished. Summary: {out}")


if __name__ == "__main__":
    main()
