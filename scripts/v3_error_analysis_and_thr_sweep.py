"""
v3 baseline tools: error analysis panels + validation threshold sweep.

Does not retrain. Uses frozen v3 checkpoint.

  python scripts/v3_error_analysis_and_thr_sweep.py
  python scripts/v3_error_analysis_and_thr_sweep.py --ckpt models/adaptformer_delhi/v3_frozen
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_split_pairs(delhi_cd: Path, split: str):
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    man = delhi_cd / split / "manifest.json"
    rows = json.loads(man.read_text(encoding="utf-8")).get("pairs", [])
    out = []
    for p in rows:
        out.append((
            _load_rgb(ROOT / p["before_path"]),
            _load_rgb(ROOT / p["after_path"]),
            _load_label(ROOT / p["gt_mask"]),
            p["pair_id"],
            split,
        ))
    return out


def _predict(model, processor, before, after, torch):
    from app.model_inference import _logits_to_change_prob
    b = np.array(Image.fromarray(before).resize((256, 256)))
    a = np.array(Image.fromarray(after).resize((256, 256)))
    inputs = processor(images=(Image.fromarray(b), Image.fromarray(a)), return_tensors="pt")
    with torch.no_grad():
        score = _logits_to_change_prob(model(**inputs).logits, torch).cpu().numpy().astype(np.float32)
    return score


def _resize_to(arr, gt, nearest=False):
    if arr.shape[:2] == gt.shape[:2]:
        return arr
    return cv2.resize(
        arr, (gt.shape[1], gt.shape[0]),
        interpolation=cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR,
    )


def _metrics(pred_bool, gt_bool):
    from app.evaluation.metrics import binary_metrics
    return binary_metrics((pred_bool.astype(np.uint8) * 255), (gt_bool.astype(np.uint8) * 255))


def _panel(before, after, gt, pred, score, title: str) -> Image.Image:
    h, w = gt.shape[:2]
    b = np.array(Image.fromarray(before).resize((w, h)))
    a = np.array(Image.fromarray(after).resize((w, h)))
    gt_rgb = np.stack([gt, gt, gt], -1)
    pred_rgb = np.stack([pred, pred, pred], -1)
    # FP=red, FN=blue, TP=green
    overlay = b.copy()
    g = gt > 127
    p = pred > 127
    overlay[p & g] = (0, 200, 0)
    overlay[p & ~g] = (255, 40, 40)   # FP
    overlay[~p & g] = (40, 80, 255)   # FN
    p_u8 = (np.clip(score, 0, 1) * 255).astype(np.uint8)
    prob_rgb = np.stack([p_u8, (p_u8 * 0.35).astype(np.uint8), (p_u8 * 0.35).astype(np.uint8)], -1)
    row = np.concatenate([b, a, gt_rgb, pred_rgb, overlay, prob_rgb], axis=1)
    img = Image.fromarray(row)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, img.width, 18], fill=(0, 0, 0))
    draw.text((4, 2), title[:120], fill=(255, 255, 255))
    # column labels
    labels = ["Before", "After", "GT", "Pred", "TP/FP/FN", "Prob"]
    for i, lab in enumerate(labels):
        draw.text((i * w + 4, 20), lab, fill=(255, 255, 0))
    return img


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", default="models/adaptformer_delhi/v3_frozen")
    parser.add_argument("--delhi-cd", default="data/delhi_cd")
    parser.add_argument("--out", default="runs/v3_baseline_analysis")
    parser.add_argument("--thr", type=float, default=None, help="override frozen thr for panels")
    args = parser.parse_args()

    import torch
    from transformers import AutoImageProcessor, AutoModel

    ckpt = ROOT / args.ckpt
    delhi_cd = ROOT / args.delhi_cd
    out = ROOT / args.out
    out.mkdir(parents=True, exist_ok=True)
    (out / "panels").mkdir(exist_ok=True)
    (out / "fp").mkdir(exist_ok=True)
    (out / "fn").mkdir(exist_ok=True)

    thr_path = ckpt / "threshold.json"
    frozen_thr = 0.2
    if thr_path.is_file():
        frozen_thr = float(json.loads(thr_path.read_text()).get("threshold", 0.2))
    panel_thr = frozen_thr if args.thr is None else float(args.thr)

    processor = AutoImageProcessor.from_pretrained(ckpt, trust_remote_code=True)
    model = AutoModel.from_pretrained(ckpt, trust_remote_code=True).eval()

    pairs = []
    for split in ("train", "val", "test"):
        pairs.extend(_load_split_pairs(delhi_cd, split))

    # Cache scores once
    cached = []
    for before, after, gt, pid, split in pairs:
        score = _predict(model, processor, before, after, torch)
        score = _resize_to(score, gt, nearest=False)
        cached.append((before, after, gt, pid, split, score))

    # --- Threshold sweep on VAL only (0.10–0.40) ---
    val_cached = [c for c in cached if c[4] == "val"]
    candidates = [round(x, 2) for x in np.arange(0.10, 0.41, 0.05)]
    sweep = []
    best = None
    print("=== VAL threshold sweep (freeze best, then test once) ===")
    for thr in candidates:
        f1s, ps, rs, ious = [], [], [], []
        for _b, _a, gt, _pid, _sp, score in val_cached:
            pred = score >= thr
            g = gt > 127
            m = _metrics(pred, g)
            f1s.append(m["f1"]); ps.append(m["precision"]); rs.append(m["recall"]); ious.append(m["iou"])
        row = {
            "threshold": thr,
            "val_f1": round(float(np.mean(f1s)), 4),
            "val_precision": round(float(np.mean(ps)), 4),
            "val_recall": round(float(np.mean(rs)), 4),
            "val_iou": round(float(np.mean(ious)), 4),
        }
        sweep.append(row)
        mark = ""
        if best is None or row["val_f1"] > best["val_f1"]:
            best = row
            mark = " *"
        print(
            f"  thr={thr:.2f} F1={row['val_f1']:.4f} P={row['val_precision']:.3f} "
            f"R={row['val_recall']:.3f} IoU={row['val_iou']:.3f}{mark}"
        )

    # Frozen thr from sweep → test once
    best_thr = float(best["threshold"])
    test_cached = [c for c in cached if c[4] == "test"]
    tf1, tp, tr, ti = [], [], [], []
    test_per = []
    for _b, _a, gt, pid, _sp, score in test_cached:
        pred = score >= best_thr
        m = _metrics(pred, gt > 127)
        tf1.append(m["f1"]); tp.append(m["precision"]); tr.append(m["recall"]); ti.append(m["iou"])
        test_per.append({"pair_id": pid, **{k: round(m[k], 4) for k in ("f1", "precision", "recall", "iou")}})
    test_summary = {
        "threshold": best_thr,
        "test_f1": round(float(np.mean(tf1)), 4),
        "test_precision": round(float(np.mean(tp)), 4),
        "test_recall": round(float(np.mean(tr)), 4),
        "test_iou": round(float(np.mean(ti)), 4),
        "per_pair": test_per,
    }
    print(
        f"\nTEST @ frozen thr={best_thr:.2f}: F1={test_summary['test_f1']:.4f} "
        f"P={test_summary['test_precision']:.3f} R={test_summary['test_recall']:.3f} "
        f"IoU={test_summary['test_iou']:.3f}"
    )

    # Also report frozen v3 thr=0.2 test for comparison
    tf1b = []
    for _b, _a, gt, pid, _sp, score in test_cached:
        m = _metrics(score >= frozen_thr, gt > 127)
        tf1b.append(m["f1"])
    print(f"TEST @ original v3 thr={frozen_thr:.2f}: F1={float(np.mean(tf1b)):.4f}")

    # --- Error analysis panels ---
    error_rows = []
    for before, after, gt, pid, split, score in cached:
        pred = (score >= panel_thr).astype(np.uint8) * 255
        g = gt > 127
        p = pred > 127
        m = _metrics(p, g)
        fp_px = int((p & ~g).sum())
        fn_px = int((~p & g).sum())
        tp_px = int((p & g).sum())
        gt_px = int(g.sum())
        miss_ratio = fn_px / max(gt_px, 1)
        fp_ratio = fp_px / max(p.sum(), 1)
        row = {
            "split": split,
            "pair_id": pid,
            "f1": round(m["f1"], 4),
            "precision": round(m["precision"], 4),
            "recall": round(m["recall"], 4),
            "iou": round(m["iou"], 4),
            "tp_px": tp_px,
            "fp_px": fp_px,
            "fn_px": fn_px,
            "miss_ratio": round(miss_ratio, 4),
            "fp_ratio": round(fp_ratio, 4),
            "dominant_error": "fn" if fn_px >= fp_px else "fp",
        }
        error_rows.append(row)
        title = (
            f"{split}/{pid} thr={panel_thr} F1={m['f1']:.3f} P={m['precision']:.3f} "
            f"R={m['recall']:.3f} FP={fp_px} FN={fn_px}"
        )
        img = _panel(before, after, gt, pred, score, title)
        img.save(out / "panels" / f"{split}_{pid}.png")
        if row["dominant_error"] == "fp" and fp_px > 50:
            img.save(out / "fp" / f"{split}_{pid}_fp{fp_px}.png")
        if row["dominant_error"] == "fn" and fn_px > 50:
            img.save(out / "fn" / f"{split}_{pid}_fn{fn_px}.png")

    # Sort hardest FN / FP for summary
    hardest_fn = sorted(error_rows, key=lambda r: (-r["miss_ratio"], -r["fn_px"]))[:12]
    hardest_fp = sorted(error_rows, key=lambda r: (-r["fp_ratio"], -r["fp_px"]))[:12]

    report = {
        "checkpoint": str(ckpt),
        "frozen_v3_threshold": frozen_thr,
        "panel_threshold": panel_thr,
        "val_sweep": sweep,
        "selected_from_val": best,
        "test_at_selected": test_summary,
        "test_at_v3_thr": {"threshold": frozen_thr, "test_f1": round(float(np.mean(tf1b)), 4)},
        "error_rows": error_rows,
        "hardest_fn": hardest_fn,
        "hardest_fp": hardest_fp,
        "n_panels": len(error_rows),
        "pdf_ops_notes": {
            "source": "DDA_Report_37_Grid_54_tif_vs_H43X2E1_tif.pdf",
            "observed": [
                "60 regions @ thr~0.2 on full GeoTIFF; change 3.08%",
                "Many low-confidence Other (26-50%) → likely FP from aggressive thr",
                "Heavy Vegetation Change share → seasonal/spectral FPs on 10m data",
                "Largest blob New Construction 107k px — verify completeness (recall)",
            ],
        },
    }
    (out / "analysis.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "recommended_threshold.json").write_text(
        json.dumps({"threshold": best_thr, "source": "val_sweep_0.10_0.40", "val": best, "test": test_summary}, indent=2),
        encoding="utf-8",
    )
    print(f"\nWrote {out}/panels ({len(error_rows)}), fp/, fn/, analysis.json")
    print("Hardest FN:", [(r["pair_id"], r["miss_ratio"], r["recall"]) for r in hardest_fn[:5]])
    print("Hardest FP:", [(r["pair_id"], r["fp_ratio"], r["precision"]) for r in hardest_fp[:5]])


if __name__ == "__main__":
    main()
