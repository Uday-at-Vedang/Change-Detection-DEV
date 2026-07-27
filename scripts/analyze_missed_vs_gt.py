"""Compare run 47 vs GT vs native for missed-change analysis."""
from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    c = sqlite3.connect(ROOT / "data/satellite_app.db")
    r = c.execute(
        "SELECT regions_json,change_percentage,regions_count,before_full_path,after_full_path,overlay_path "
        "FROM detection_runs WHERE id=47"
    ).fetchone()
    regs = json.loads(r[0])
    print("run47 change%", r[1], "n", r[2])
    print(Counter(x.get("objectType") for x in regs))
    for x in regs:
        print(
            f"  #{x['id']} {x.get('objectType')} conf={float(x.get('confidence') or 0):.2f} "
            f"area={x['area']} bbox={x['bbox']}"
        )

    gt_path = ROOT / "docs/delhi_eval/labels/dda_grid54_h43x2e1.png"
    g = np.array(Image.open(gt_path).convert("L")) > 127
    print("GT shape", g.shape, "change%", round(g.mean() * 100, 4), "px", int(g.sum()))
    lab, n = ndimage.label(g)
    print("GT components", n)

    before = np.array(Image.open(ROOT / "data" / r[3]).convert("RGB"))
    # overlay-sized working image
    bh, bw = before.shape[:2]
    gt_r = np.array(Image.fromarray(g.astype(np.uint8) * 255).resize((bw, bh), Image.NEAREST)) > 127

    # Approximate pred mask from region bboxes (coarse) — better: recover from overlay tint
    overlay = np.array(Image.open(ROOT / "data" / r[5]).convert("RGB"))
    after = np.array(Image.open(ROOT / "data" / r[4]).convert("RGB"))
    # Tint recovery: changed pixels pushed toward red
    diff = overlay.astype(np.float32) - after.astype(np.float32)
    pred = (diff[:, :, 0] > 8) & (diff[:, :, 0] > diff[:, :, 1] + 3)
    # Dilate slightly
    pred = ndimage.binary_dilation(pred, iterations=1)
    print("pred_from_overlay change%", round(pred.mean() * 100, 4), "px", int(pred.sum()))

    inter = (gt_r & pred).sum()
    gt_px = max(int(gt_r.sum()), 1)
    pred_px = max(int(pred.sum()), 1)
    print("GT recall vs overlay tint", round(inter / gt_px, 4), "precision", round(inter / pred_px, 4))

    # Per GT component: covered?
    glab, gn = ndimage.label(gt_r)
    missed = []
    for i in range(1, gn + 1):
        comp = glab == i
        area = int(comp.sum())
        cov = float((comp & pred).sum() / max(area, 1))
        ys, xs = np.where(comp)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1))
        status = "HIT" if cov >= 0.15 else "MISS"
        print(f"  GT#{i} area={area} cov={cov:.2f} {status} bbox={bbox}")
        if status == "MISS":
            missed.append((i, area, bbox, cov))

    # Save miss crops for inspection
    out = ROOT / "runs/missed_gt_review"
    out.mkdir(parents=True, exist_ok=True)
    for i, area, bbox, cov in missed:
        x0, y0, bw, bh = bbox
        pad = 40
        xa, ya = max(0, x0 - pad), max(0, y0 - pad)
        xb, yb = min(before.shape[1], x0 + bw + pad), min(before.shape[0], y0 + bh + pad)
        panel = np.concatenate([before[ya:yb, xa:xb], after[ya:yb, xa:xb]], axis=1)
        Image.fromarray(panel).save(out / f"miss_gt{i}_a{area}.png")
    print("missed", len(missed), "->", out)

    ns = ROOT / "runs/native_dda/20260721_131449/native/summary.json"
    if ns.exists():
        s = json.loads(ns.read_text(encoding="utf-8"))
        print("native change%", s["stats"].get("change_percentage"), "regions", s.get("n_regions"))


if __name__ == "__main__":
    main()
