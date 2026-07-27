"""
Mine hard-negative tiles (vegetation / seasonal non-change) for AdaptFormer.

For each labeled Delhi pair, run the frozen v3 model at 256². Where the model
fires but GT is empty (false positives), save before/after/empty-gt crops into
``data/delhi_cd/hard_negatives/`` and append them to the train manifest.

Usage:
  python scripts/mine_hard_negatives.py
  python scripts/mine_hard_negatives.py --max-pairs 12 --thr 0.2
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data/delhi_cd/hard_negatives"
TRAIN_MAN = ROOT / "data/delhi_cd/train/manifest.json"


def _load_model(ckpt: Path):
    import torch
    from transformers import AutoModel, AutoImageProcessor
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    processor = AutoImageProcessor.from_pretrained(str(ckpt), trust_remote_code=True)
    model = AutoModel.from_pretrained(
        str(ckpt), trust_remote_code=True).to(device).eval()
    return model, processor, device, torch


def _predict(model, processor, device, torch, before, after):
    from app.model_inference import _logits_to_change_prob
    b = np.array(Image.fromarray(before).resize((256, 256)))
    a = np.array(Image.fromarray(after).resize((256, 256)))
    inputs = processor(images=(Image.fromarray(b), Image.fromarray(a)), return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        score = _logits_to_change_prob(model(**inputs).logits, torch).cpu().numpy().astype(np.float32)
    if score.ndim == 3:
        score = score[0]
    return score, b, a


def _is_veg_pair(pair: dict) -> bool:
    types = pair.get("change_types") or []
    return "vegetation" in types or "mixed_gsd" in types


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ckpt", default="models/adaptformer_delhi/v3_frozen")
    ap.add_argument("--thr", type=float, default=0.2)
    ap.add_argument("--max-pairs", type=int, default=24)
    ap.add_argument("--min-fp-frac", type=float, default=0.04,
                    help="Min FP pixel fraction to keep a hard-neg tile")
    ap.add_argument("--max-gt-frac", type=float, default=0.02,
                    help="Skip pairs/tiles that already have real change")
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
    from app.evaluation.delhi_eval import _load_label, _load_rgb

    ckpt = ROOT / args.ckpt
    if not (ckpt / "config.json").is_file():
        print(f"Missing checkpoint {ckpt}")
        return 1

    man = json.loads((ROOT / "docs/delhi_eval/manifest.json").read_text(encoding="utf-8"))
    pairs = [p for p in man.get("pairs", []) if p.get("gt_mask") and _is_veg_pair(p)]
    pairs = pairs[: args.max_pairs]
    print(f"Scanning {len(pairs)} vegetation/mixed pairs for hard negatives...")

    model, processor, device, torch = _load_model(ckpt)
    OUT.mkdir(parents=True, exist_ok=True)
    saved = []

    for p in pairs:
        pid = p["pair_id"]
        try:
            before = _load_rgb(ROOT / p["before_path"])
            after = _load_rgb(ROOT / p["after_path"])
            gt = _load_label(ROOT / p["gt_mask"])
        except Exception as exc:
            print(f"  skip {pid}: {exc}")
            continue
        score, b256, a256 = _predict(model, processor, device, torch, before, after)
        gt256 = np.array(Image.fromarray(gt).resize((256, 256), Image.NEAREST))
        pred = score >= args.thr
        g = gt256 > 127
        fp = pred & (~g)
        fp_frac = float(fp.mean())
        gt_frac = float(g.mean())
        print(f"  {pid}: gt={gt_frac:.3f} fp={fp_frac:.3f}")
        if gt_frac > args.max_gt_frac:
            continue
        if fp_frac < args.min_fp_frac:
            continue
        # Save full 256 tile as hard-neg (empty GT)
        hn_id = f"hn_{pid}"
        before_p = OUT / f"{hn_id}_before.png"
        after_p = OUT / f"{hn_id}_after.png"
        gt_p = OUT / f"{hn_id}_gt.png"
        Image.fromarray(b256).save(before_p)
        Image.fromarray(a256).save(after_p)
        Image.fromarray(np.zeros((256, 256), dtype=np.uint8)).save(gt_p)
        # Also save FP overlay for QA
        vis = a256.copy()
        vis[fp] = (255, 40, 40)
        Image.fromarray(vis).save(OUT / f"{hn_id}_fp_overlay.png")
        saved.append({
            "pair_id": hn_id,
            "before_path": str(before_p.relative_to(ROOT)).replace("\\", "/"),
            "after_path": str(after_p.relative_to(ROOT)).replace("\\", "/"),
            "gt_mask": str(gt_p.relative_to(ROOT)).replace("\\", "/"),
            "change_types": ["hard_negative", "vegetation_seasonal"],
            "notes": f"Mined FP from {pid} (fp_frac={fp_frac:.3f})",
        })

    if not saved:
        print("No hard negatives mined — try lowering --min-fp-frac")
        return 0

    # Append to train manifest
    train = json.loads(TRAIN_MAN.read_text(encoding="utf-8"))
    existing = {p.get("pair_id") for p in train.get("pairs", [])}
    added = 0
    for row in saved:
        if row["pair_id"] in existing:
            continue
        train.setdefault("pairs", []).append(row)
        added += 1
    TRAIN_MAN.write_text(json.dumps(train, indent=2), encoding="utf-8")

    # Update pair_ids.txt
    ids_path = ROOT / "data/delhi_cd/train/pair_ids.txt"
    ids = [ln.strip() for ln in ids_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    for row in saved:
        if row["pair_id"] not in ids:
            ids.append(row["pair_id"])
    ids_path.write_text("\n".join(ids) + "\n", encoding="utf-8")

    summary = {
        "n_saved": len(saved),
        "n_added_to_train": added,
        "thr": args.thr,
        "ckpt": str(ckpt),
        "pairs": saved,
        "created_unix": time.time(),
    }
    (OUT / "mining_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"Saved {len(saved)} hard-neg tiles; added {added} to train manifest -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
