"""One-shot threshold sweep on existing v2 checkpoint (val-only select, then test)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main():
    import torch
    from transformers import AutoImageProcessor, AutoModel
    from app.model_inference import _logits_to_change_prob
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    from app.evaluation.metrics import binary_metrics

    ckpt = ROOT / "runs" / "finetune_v2" / "20260716_210208" / "best"
    proc = AutoImageProcessor.from_pretrained(ckpt, trust_remote_code=True)
    model = AutoModel.from_pretrained(ckpt, trust_remote_code=True).eval()

    def load_split(name):
        rows = json.loads((ROOT / "data" / "delhi_cd" / name / "manifest.json").read_text())["pairs"]
        out = []
        for p in rows:
            b = _load_rgb(ROOT / p["before_path"])
            a = _load_rgb(ROOT / p["after_path"])
            g = _load_label(ROOT / p["gt_mask"])
            out.append((b, a, g, p["pair_id"]))
        return out

    def predict(b, a):
        b256 = np.array(Image.fromarray(b).resize((256, 256)))
        a256 = np.array(Image.fromarray(a).resize((256, 256)))
        inputs = proc(images=(Image.fromarray(b256), Image.fromarray(a256)), return_tensors="pt")
        with torch.no_grad():
            return _logits_to_change_prob(model(**inputs).logits, torch).cpu().numpy().astype(np.float32)

    def eval_at(pairs, thr):
        f1s, ps, rs, ious, per = [], [], [], [], []
        for b, a, gt, pid in pairs:
            score = predict(b, a)
            score = cv2.resize(score, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_LINEAR)
            mask = (score >= thr).astype(np.uint8) * 255
            m = binary_metrics(mask, gt)
            f1s.append(m["f1"]); ps.append(m["precision"]); rs.append(m["recall"]); ious.append(m["iou"])
            per.append((pid, m))
        return dict(
            f1=float(np.mean(f1s)), P=float(np.mean(ps)), R=float(np.mean(rs)),
            IoU=float(np.mean(ious)), per=per,
        )

    val, test = load_split("val"), load_split("test")
    print("=== VAL threshold sweep 0.20-0.70 (select on F1) ===")
    best = None
    best_fbeta = None
    beta = 1.5
    b2 = beta * beta
    for thr in np.linspace(0.20, 0.70, 26):
        r = eval_at(val, float(thr))
        fbeta = 0.0 if (r["P"] + r["R"]) == 0 else (1 + b2) * r["P"] * r["R"] / (b2 * r["P"] + r["R"])
        mark = ""
        if best is None or r["f1"] > best[1]:
            best = (float(thr), r["f1"], r["P"], r["R"])
            mark += " *F1"
        if best_fbeta is None or fbeta > best_fbeta[1]:
            best_fbeta = (float(thr), fbeta, r["f1"], r["P"], r["R"])
            mark += " *Fb"
        print(f"thr={thr:.3f} F1={r['f1']:.4f} P={r['P']:.3f} R={r['R']:.3f} Fb={fbeta:.4f}{mark}")

    print("BEST VAL by F1:", best)
    print("BEST VAL by Fbeta:", best_fbeta)

    for label, thr in [
        ("baseline 0.50", 0.50),
        ("best-F1", best[0]),
        ("best-Fbeta", best_fbeta[0]),
        ("recall-push 0.35", 0.35),
        ("recall-push 0.30", 0.30),
        ("recall-push 0.25", 0.25),
    ]:
        print(f"\n=== TEST at {label} thr={thr:.3f} ===")
        r = eval_at(test, thr)
        print(f"F1={r['f1']:.4f} P={r['P']:.3f} R={r['R']:.3f} IoU={r['IoU']:.3f}")
        for pid, m in r["per"]:
            print(f"  {pid}: F1={m['f1']:.3f} P={m['precision']:.3f} R={m['recall']:.3f}")


if __name__ == "__main__":
    main()
