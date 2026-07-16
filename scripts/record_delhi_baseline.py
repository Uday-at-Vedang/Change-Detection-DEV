"""
Record Delhi baseline metrics (Day 2 deliverable) for default settings.

Runs each primary method separately at sensitivity 0.5 (one process at a time
to avoid RAM spikes) and writes runs/delhi_baseline/metrics.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "runs" / "delhi_baseline"
MANIFEST = ROOT / "docs" / "delhi_eval" / "manifest.json"
METHODS = [
    "Feature-Based",
    "KPCA (Unsupervised)",
    "Hybrid Approach",
    "AI-Based Deep Learning",
    "Hybrid AI",
]


def _run_method(method: str) -> list[dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    report = OUT / f"report_{method.replace(' ', '_')}.json"
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "compare_methods.py"),
        "--manifest", str(MANIFEST),
        "--methods", method,
        "--sensitivities", "0.5",
        "--out", str(OUT),
        "--report-only",
    ]
    print(f"\n=== Baseline: {method} ===")
    subprocess.run(cmd, check=True, cwd=ROOT)
    manifest_report = OUT / "manifest_report.json"
    if manifest_report.is_file():
        rows = json.loads(manifest_report.read_text(encoding="utf-8"))
        report.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        return rows
    return []


def main():
    all_rows: list[dict] = []
    for method in METHODS:
        all_rows.extend(_run_method(method))

    labeled = [r for r in all_rows if "f1" in r]
    summary = {
        "methods": METHODS,
        "sensitivity": 0.5,
        "n_pairs": len({r["pair_id"] for r in all_rows}),
        "n_labeled_rows": len(labeled),
        "per_method_mean_f1": {},
        "per_method_mean_iou": {},
    }
    by_method: dict[str, list[dict]] = {}
    for r in labeled:
        by_method.setdefault(r["method"], []).append(r)
    for m, rows in by_method.items():
        summary["per_method_mean_f1"][m] = round(
            sum(r["f1"] for r in rows) / len(rows), 4)
        summary["per_method_mean_iou"][m] = round(
            sum(r["iou"] for r in rows) / len(rows), 4)

    (OUT / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "manifest_report.json").write_text(json.dumps(all_rows, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT / 'metrics.json'}")
    for m, f1 in summary["per_method_mean_f1"].items():
        print(f"  {m}: mean F1={f1}  IoU={summary['per_method_mean_iou'][m]}")


if __name__ == "__main__":
    main()
