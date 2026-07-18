"""Analyze false negatives from a fine-tuned AdaptFormer checkpoint.

Categorizes missed change components (small / large / thin) and writes
Before|GT|Pred|TP-green/FN-red/FP-blue panels.

Usage:
  python scripts/analyze_adaptformer_fn.py --ckpt runs/finetune_v2/20260716_210208/best
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ckpt", required=True)
    parser.add_argument("--delhi-cd", default="data/delhi_cd")
    parser.add_argument("--thr", type=float, default=None)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    import torch
    from transformers import AutoImageProcessor, AutoModel
    from app.model_inference import _logits_to_change_prob
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    from app.evaluation.metrics import binary_metrics

    ckpt = Path(args.ckpt)
    if not ckpt.is_dir():
        raise SystemExit(f"Missing checkpoint {ckpt}")
    thr = args.thr
    if thr is None:
        thr_path = ckpt / "threshold.json"
        thr = float(json.loads(thr_path.read_text()).get("threshold", 0.5)) if thr_path.is_file() else 0.5

    out = Path(args.out) if args.out else ckpt.parent / "fn_analysis"
    out.mkdir(parents=True, exist_ok=True)

    proc = AutoImageProcessor.from_pretrained(ckpt, trust_remote_code=True)
    model = AutoModel.from_pretrained(ckpt, trust_remote_code=True).eval()

    cats = {k: 0 for k in ("small_blob", "large_blob", "thin_linear", "missed_almost_all", "partial", "ok")}
    summary = []
    delhi_cd = Path(args.delhi_cd)
    for split in ("train", "val", "test"):
        man = delhi_cd / split / "manifest.json"
        if not man.is_file():
            continue
        for p in json.loads(man.read_text()).get("pairs", []):
            before = _load_rgb(ROOT / p["before_path"])
            after = _load_rgb(ROOT / p["after_path"])
            gt = _load_label(ROOT / p["gt_mask"])
            b256 = np.array(Image.fromarray(before).resize((256, 256)))
            a256 = np.array(Image.fromarray(after).resize((256, 256)))
            inputs = proc(images=(Image.fromarray(b256), Image.fromarray(a256)), return_tensors="pt")
            with torch.no_grad():
                prob = _logits_to_change_prob(model(**inputs).logits, torch).cpu().numpy()
            if prob.shape != gt.shape[:2]:
                pred = cv2.resize((prob >= thr).astype(np.uint8), (gt.shape[1], gt.shape[0]),
                                  interpolation=cv2.INTER_NEAREST).astype(bool)
            else:
                pred = prob >= thr
            g = gt > 127
            m = binary_metrics((pred.astype(np.uint8) * 255), gt)
            fn = g & ~pred
            fp = pred & ~g
            tp = pred & g
            n_labels, lab, stats, _ = cv2.connectedComponentsWithStats(g.astype(np.uint8), 8)
            fn_small = fn_large = fn_thin = missed_comp = 0
            for i in range(1, n_labels):
                area = int(stats[i, cv2.CC_STAT_AREA])
                w = int(stats[i, cv2.CC_STAT_WIDTH])
                h = int(stats[i, cv2.CC_STAT_HEIGHT])
                comp = lab == i
                recall_c = float((pred & comp).sum()) / max(area, 1)
                aspect = max(w, h) / max(min(w, h), 1)
                if recall_c < 0.2:
                    missed_comp += 1
                    if area < 40:
                        fn_small += 1
                    elif aspect >= 3:
                        fn_thin += 1
                    else:
                        fn_large += 1
            miss_ratio = float(fn.sum()) / max(int(g.sum()), 1)
            if miss_ratio < 0.25:
                cat = "ok"
            elif miss_ratio > 0.75:
                cat = "missed_almost_all"
            elif fn_small >= fn_large and fn_small >= fn_thin:
                cat = "small_blob"
            elif fn_thin > fn_large:
                cat = "thin_linear"
            else:
                cat = "partial"
            cats[cat] += 1
            row = {
                "split": split,
                "pair_id": p["pair_id"],
                "f1": round(m["f1"], 4),
                "precision": round(m["precision"], 4),
                "recall": round(m["recall"], 4),
                "gt_frac": round(float(g.mean()), 4),
                "miss_ratio": round(miss_ratio, 3),
                "n_gt_comp": max(0, n_labels - 1),
                "missed_comp": missed_comp,
                "fn_small": fn_small,
                "fn_large": fn_large,
                "fn_thin": fn_thin,
                "category": cat,
            }
            summary.append(row)
            if split in ("test", "val") or miss_ratio > 0.5:
                overlay = np.zeros((*g.shape, 3), np.uint8)
                overlay[tp] = (0, 200, 0)
                overlay[fn] = (255, 0, 0)
                overlay[fp] = (0, 0, 255)
                before_r = np.array(Image.fromarray(before).resize((g.shape[1], g.shape[0])))
                gt_rgb = np.stack([gt, gt, gt], axis=-1)
                pred_rgb = np.stack([pred.astype(np.uint8) * 255] * 3, axis=-1)
                panel = np.concatenate([before_r, gt_rgb, pred_rgb, overlay], axis=1)
                Image.fromarray(panel).save(out / f"{split}_{p['pair_id']}_{cat}.png")

    report = {"threshold": thr, "category_counts": cats, "pairs": summary}
    (out / "fn_summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("FN categories:", cats)
    print("Test pairs:")
    for r in summary:
        if r["split"] == "test":
            print(f"  {r['pair_id']}: F1={r['f1']} P={r['precision']} R={r['recall']} cat={r['category']}")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
