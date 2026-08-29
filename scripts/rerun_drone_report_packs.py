"""Re-run detection on drone GT packs and save overlays + summary (report redo).

Library GeoTIFFs for reports 54–59 were not present locally; this uses the
ingested labeling packs (same scenes as before3/4/5/6/1). before7 has no pack.
"""
from __future__ import annotations

import json
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
    ("report_55_style", "dda_before1_after"),
    ("report_54_style", "dda_before3_after3"),
    ("report_56_style", "dda_before4_after4"),
    ("report_57_style", "dda_before5_after5"),
    ("report_58_style", "dda_before6_after6"),
]


def main():
    from app.detection_engine import run_detection

    out = ROOT / "data/delhi_cd/friday_drone_report_fix/rerun_overlays"
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    for tag, pid in PACKS:
        pack = ROOT / "docs/delhi_eval/dda_labeling" / pid
        before = Image.open(pack / "before.png").convert("RGB")
        after = Image.open(pack / "after.png").convert("RGB")
        if after.size != before.size:
            after = after.resize(before.size, Image.Resampling.LANCZOS)
        gt_p = ROOT / "docs/delhi_eval/labels" / f"{pid}.png"
        t0 = time.time()
        mask, vis, stats, regions = run_detection(
            before, after,
            method="AI-Based Deep Learning",
            enable_registration=True,
            enable_normalization=True,
            detection_sensitivity=0.5,
            min_region_area=150,
            max_size=max(before.size),
        )
        elapsed = time.time() - t0
        Image.fromarray(vis).save(out / f"{pid}_overlay.png")
        pred = np.asarray(mask)
        if pred.ndim == 3:
            pred = pred[..., 0]
        pred = pred > 127
        gt = np.array(Image.open(gt_p).convert("L")) > 127
        if pred.shape != gt.shape:
            gt = np.array(Image.fromarray(gt.astype(np.uint8) * 255).resize(
                (pred.shape[1], pred.shape[0]), Image.Resampling.NEAREST)) > 0
        p, g = pred.ravel(), gt.ravel()
        tp = int(np.logical_and(p, g).sum())
        fp = int(np.logical_and(p, ~g).sum())
        fn = int(np.logical_and(~p, g).sum())
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = (2 * prec * rec / (prec + rec)) if prec + rec else 0.0
        row = {
            "tag": tag,
            "pair_id": pid,
            "elapsed_s": round(elapsed, 2),
            "change_pct": round(float(stats.get("change_percentage") or 0), 3),
            "gt_pct": round(100 * float(g.mean()), 3),
            "n_regions": len(regions or []),
            "f1": round(f1, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "registration": (stats.get("params") or {}).get("registration"),
            "threshold_debug": {
                k: stats.get("threshold_debug", {}).get(k)
                for k in (
                    "pair_ncc", "drone_fp_mode", "drone_tta",
                    "drone_overfire_clean", "threshold_score",
                )
            },
            "overlay": str((out / f"{pid}_overlay.png").relative_to(ROOT)).replace("\\", "/"),
        }
        rows.append(row)
        print(
            f"{pid}: F1={row['f1']:.3f} change%={row['change_pct']:.2f} "
            f"gt%={row['gt_pct']:.2f} regions={row['n_regions']}",
            flush=True,
        )

    summary = {
        "created_unix": time.time(),
        "note": "before7 has no GT pack; library TIFs for reports 54-59 not on disk",
        "mean_f1": round(float(np.mean([r["f1"] for r in rows])), 4),
        "pairs": rows,
    }
    (out.parent / "rerun_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    print("mean_f1", summary["mean_f1"], "->", out.parent / "rerun_summary.json")


if __name__ == "__main__":
    main()
