"""Compare DSIFN vs AdaptFormer boundary completeness on Delhi pairs.

Metrics per GT connected component (building-like blobs):
  - fill_ratio: pred∩gt / gt  (interior fill)
  - hole_rate: 1 - fill inside eroded-gt core (holes in interiors)
  - boundary_f1: F1 on a 3px boundary band around GT

Also reports pair-level F1/P/R.

Usage:
    python scripts/compare_dsifn_vs_adaptformer.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

OUT = ROOT / "data" / "delhi_cd" / "thursday_dsifn_compare"
DSIFN_CKPT = ROOT / "models" / "dsifn_proxy" / "best.pt"
DSIFN_PY = ROOT / "third_party" / "DSIFN" / "pytorch version" / "DSIFN.py"
DSIFN_TEST = ROOT / "third_party" / "DSIFN_weights" / "data" / "test"
ADAPT_CKPT = ROOT / "models" / "adaptformer_delhi" / "wed_retrain"
# Fall back to v3 if wed missing
if not (ADAPT_CKPT / "model.safetensors").is_file():
    ADAPT_CKPT = ROOT / "models" / "adaptformer_delhi" / "v3_frozen"

DELHI_IDS = ["delhi_0024", "delhi_0001", "delhi_0005", "delhi_0016", "delhi_0021"]  # 4 test + 1 val


def _load_dsifn():
    spec = importlib.util.spec_from_file_location("dsifn_official", DSIFN_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    ckpt = torch.load(DSIFN_CKPT, map_location=device, weights_only=False)
    size = int(ckpt.get("size", 256))
    try:
        from torchvision.models import VGG16_Weights
        features = list(__import__("torchvision").models.vgg16(weights=VGG16_Weights.DEFAULT).features)[:30]
        def make_base():
            base = mod.vgg16_base.__new__(mod.vgg16_base)
            torch.nn.Module.__init__(base)
            base.features = torch.nn.ModuleList(features).eval()
            return base
        model = mod.DSIFN(make_base(), make_base()).to(device)
    except Exception:
        model = mod.DSIFN(mod.vgg16_base(), mod.vgg16_base()).to(device)
    model.load_state_dict(ckpt["model"], strict=False)
    model.eval()
    return model, device, size


@torch.no_grad()
def predict_dsifn(model, device, size, before: np.ndarray, after: np.ndarray) -> np.ndarray:
    h, w = before.shape[:2]
    def prep(img):
        im = Image.fromarray(img).convert("RGB").resize((size, size), Image.BILINEAR)
        t = torch.from_numpy(np.asarray(im).transpose(2, 0, 1)).float() / 255.0
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        return ((t - mean) / std).unsqueeze(0).to(device)
    outs = model(prep(before), prep(after))
    score = outs[0][0, 0].detach().cpu().numpy().astype(np.float32)
    score = cv2.resize(score, (w, h), interpolation=cv2.INTER_LINEAR)
    return score


def predict_adaptformer(before: np.ndarray, after: np.ndarray) -> np.ndarray:
    os.environ["ADAPTFORMER_WEIGHTS"] = str(ADAPT_CKPT)
    os.environ["DETECTION_TTA"] = "off"
    os.environ["DETECTION_MULTISCALE"] = "off"
    os.environ["DETECTION_FUSION"] = "dl_only"
    import app.model_inference as mi
    mi._MODEL = None
    mi._PROCESSOR = None
    mi._LOADED_FROM = None
    mi._CALIBRATED_THRESHOLD = None
    from app.model_inference import predict_change_mask, preload_model
    preload_model()
    _m, score = predict_change_mask(before, after, threshold=2.0)
    return score.astype(np.float32)


def _metrics(pred: np.ndarray, gt: np.ndarray, thr: float = 0.5) -> dict:
    gt_b = gt.astype(bool)
    pr = pred >= thr
    if not gt_b.any():
        return {"f1": 0.0, "precision": 0.0, "recall": 0.0, "fill_ratio": 0.0,
                "hole_rate": 0.0, "boundary_f1": 0.0, "n_components": 0}
    tp = int((pr & gt_b).sum()); fp = int((pr & ~gt_b).sum()); fn = int((~pr & gt_b).sum())
    p = 0.0 if tp + fp == 0 else tp / (tp + fp)
    r = 0.0 if tp + fn == 0 else tp / (tp + fn)
    f1 = 0.0 if p + r == 0 else 2 * p * r / (p + r)

    # Component-wise fill
    num, labels, stats, _ = cv2.connectedComponentsWithStats(gt_b.astype(np.uint8), 8)
    fills = []
    holes = []
    for i in range(1, num):
        comp = labels == i
        area = int(comp.sum())
        if area < 20:
            continue
        fill = float((pr & comp).sum()) / float(area)
        fills.append(fill)
        # hole: miss rate inside morphologically eroded core
        core = cv2.erode(comp.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=1).astype(bool)
        if core.any():
            holes.append(float((~pr & core).sum()) / float(core.sum()))
    fill_ratio = float(np.mean(fills)) if fills else 0.0
    hole_rate = float(np.mean(holes)) if holes else 0.0

    # Boundary band F1
    band = cv2.dilate(gt_b.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1).astype(bool) & (
        ~cv2.erode(gt_b.astype(np.uint8), np.ones((5, 5), np.uint8), iterations=1).astype(bool)
    )
    if band.any():
        bt = int((pr & band & gt_b).sum())  # not quite — use band as ROI
        # Treat band pixels that are GT-boundary: compare pred vs gt on band
        btp = int((pr & gt_b & band).sum())
        bfp = int((pr & ~gt_b & band).sum())
        bfn = int((~pr & gt_b & band).sum())
        bp = 0.0 if btp + bfp == 0 else btp / (btp + bfp)
        br = 0.0 if btp + bfn == 0 else btp / (btp + bfn)
        bf1 = 0.0 if bp + br == 0 else 2 * bp * br / (bp + br)
    else:
        bf1 = 0.0

    return {
        "f1": round(f1, 4),
        "precision": round(p, 4),
        "recall": round(r, 4),
        "fill_ratio": round(fill_ratio, 4),
        "hole_rate": round(hole_rate, 4),
        "boundary_f1": round(bf1, 4),
        "n_components": len(fills),
    }


def _load_delhi_pair(pair_id: str):
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    man = json.loads((ROOT / "data/delhi_cd/test/manifest.json").read_text(encoding="utf-8"))
    rows = {p["pair_id"]: p for p in man["pairs"]}
    if pair_id not in rows:
        manv = json.loads((ROOT / "data/delhi_cd/val/manifest.json").read_text(encoding="utf-8"))
        rows.update({p["pair_id"]: p for p in manv["pairs"]})
    p = rows[pair_id]
    before = _load_rgb(ROOT / p["before_path"])
    after = _load_rgb(ROOT / p["after_path"])
    gt = _load_label(ROOT / p["gt_mask"])
    return before, after, gt


def _dsifn_test_pairs(n: int = 5):
    items = []
    t1 = DSIFN_TEST / "t1"
    for p in sorted(t1.glob("*"))[:n]:
        stem = p.stem
        t2 = next((DSIFN_TEST / "t2").glob(f"{stem}.*"))
        m = next((DSIFN_TEST / "mask").glob(f"{stem}.*"))
        before = np.asarray(Image.open(p).convert("RGB"))
        after = np.asarray(Image.open(t2).convert("RGB"))
        gt = np.asarray(Image.open(m).convert("L"))
        items.append((stem, before, after, gt))
    return items


def _gt_bool(gt: np.ndarray) -> np.ndarray:
    # DSIFN masks are often 0/1; Delhi PNGs are 0/255
    return gt > (0 if gt.max() <= 1 else 127)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    if not DSIFN_CKPT.is_file():
        print(f"Missing DSIFN proxy ckpt: {DSIFN_CKPT}")
        print("Run: python scripts/dsifn_proxy_train.py --epochs 5")
        return 1

    print("Loading DSIFN...", flush=True)
    dsifn, device, size = _load_dsifn()
    print(f"DSIFN ready size={size} device={device}", flush=True)

    # --- DSIFN test tiles (proxy pretrained eval) ---
    print("\n=== DSIFN-CD test tiles ===", flush=True)
    dsifn_rows = []
    for stem, before, after, gt in _dsifn_test_pairs(8):
        t0 = time.perf_counter()
        score = predict_dsifn(dsifn, device, size, before, after)
        m = _metrics(score, _gt_bool(gt), thr=0.5)
        m["id"] = stem
        m["elapsed_s"] = round(time.perf_counter() - t0, 2)
        dsifn_rows.append(m)
        print(f"  {stem}: F1={m['f1']} fill={m['fill_ratio']} hole={m['hole_rate']}", flush=True)

    # --- Delhi pairs: DSIFN vs AdaptFormer ---
    print("\n=== Delhi pairs ===", flush=True)
    delhi_compare = []
    thr_adapt = 0.446
    thr_path = ADAPT_CKPT / "threshold.json"
    if thr_path.is_file():
        thr_adapt = float(json.loads(thr_path.read_text(encoding="utf-8")).get("threshold", thr_adapt))

    for pid in DELHI_IDS:
        try:
            before, after, gt = _load_delhi_pair(pid)
        except Exception as e:
            print(f"  skip {pid}: {e}", flush=True)
            continue
        gt_b = _gt_bool(gt)
        t0 = time.perf_counter()
        s_d = predict_dsifn(dsifn, device, size, before, after)
        t_d = time.perf_counter() - t0
        t0 = time.perf_counter()
        s_a = predict_adaptformer(before, after)
        t_a = time.perf_counter() - t0
        if s_a.shape[:2] != gt_b.shape[:2]:
            s_a = cv2.resize(s_a, (gt_b.shape[1], gt_b.shape[0]), interpolation=cv2.INTER_LINEAR)
        if s_d.shape[:2] != gt_b.shape[:2]:
            s_d = cv2.resize(s_d, (gt_b.shape[1], gt_b.shape[0]), interpolation=cv2.INTER_LINEAR)

        md = _metrics(s_d, gt_b, thr=0.5)
        ma = _metrics(s_a, gt_b, thr=thr_adapt)
        row = {
            "pair_id": pid,
            "dsifn": {**md, "elapsed_s": round(t_d, 2)},
            "adaptformer": {**ma, "elapsed_s": round(t_a, 2), "thr": thr_adapt},
            "fill_delta_adapt_minus_dsifn": round(ma["fill_ratio"] - md["fill_ratio"], 4),
            "hole_delta_adapt_minus_dsifn": round(ma["hole_rate"] - md["hole_rate"], 4),
        }
        delhi_compare.append(row)
        print(
            f"  {pid}: AF fill={ma['fill_ratio']} hole={ma['hole_rate']} F1={ma['f1']} | "
            f"DSIFN fill={md['fill_ratio']} hole={md['hole_rate']} F1={md['f1']}",
            flush=True,
        )
        # save overlays
        vis_dir = OUT / "overlays" / pid
        vis_dir.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(vis_dir / "gt.png"), (gt_b.astype(np.uint8) * 255))
        cv2.imwrite(str(vis_dir / "dsifn.png"), ((s_d >= 0.5).astype(np.uint8) * 255))
        cv2.imwrite(str(vis_dir / "adaptformer.png"), ((s_a >= thr_adapt).astype(np.uint8) * 255))

    # Decision
    if delhi_compare:
        mean_af_fill = float(np.mean([r["adaptformer"]["fill_ratio"] for r in delhi_compare]))
        mean_ds_fill = float(np.mean([r["dsifn"]["fill_ratio"] for r in delhi_compare]))
        mean_af_hole = float(np.mean([r["adaptformer"]["hole_rate"] for r in delhi_compare]))
        mean_ds_hole = float(np.mean([r["dsifn"]["hole_rate"] for r in delhi_compare]))
        mean_af_f1 = float(np.mean([r["adaptformer"]["f1"] for r in delhi_compare]))
        mean_ds_f1 = float(np.mean([r["dsifn"]["f1"] for r in delhi_compare]))
    else:
        mean_af_fill = mean_ds_fill = mean_af_hole = mean_ds_hole = mean_af_f1 = mean_ds_f1 = 0.0

    # Prefer AdaptFormer if it fills interiors better (higher fill, lower hole) OR similar fill with higher F1
    keep_adaptformer = (mean_af_fill >= mean_ds_fill - 0.02) and (mean_af_f1 >= mean_ds_f1 - 0.03)
    if mean_ds_fill > mean_af_fill + 0.05 and mean_ds_hole + 0.05 < mean_af_hole:
        decision = "CONSIDER_DSIFN"
        rationale = (
            f"DSIFN fills interiors better on Delhi (fill {mean_ds_fill:.3f} vs {mean_af_fill:.3f}, "
            f"hole {mean_ds_hole:.3f} vs {mean_af_hole:.3f}). Worth a deeper integration spike."
        )
    elif keep_adaptformer:
        decision = "KEEP_ADAPTFORMER"
        rationale = (
            f"AdaptFormer matches/beats DSIFN on Delhi interior fill "
            f"(fill {mean_af_fill:.3f} vs {mean_ds_fill:.3f}, hole {mean_af_hole:.3f} vs {mean_ds_hole:.3f}, "
            f"F1 {mean_af_f1:.3f} vs {mean_ds_f1:.3f}). Do not switch backbones; keep AdaptFormer."
        )
    else:
        decision = "KEEP_ADAPTFORMER_MONITOR"
        rationale = (
            f"Mixed: AF F1={mean_af_f1:.3f} fill={mean_af_fill:.3f}; DSIFN F1={mean_ds_f1:.3f} "
            f"fill={mean_ds_fill:.3f}. Stay on AdaptFormer; revisit if building-interior misses persist."
        )

    summary = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Official DSIFN pretrained weights are NOT in the public Google Drive package "
            "(only train/val/test zips). Comparison uses a proxy DSIFN trained on val→eval test."
        ),
        "dsifn_ckpt": str(DSIFN_CKPT),
        "adaptformer_ckpt": str(ADAPT_CKPT),
        "dsifn_test_sample": dsifn_rows,
        "delhi": delhi_compare,
        "means": {
            "adaptformer_fill": round(mean_af_fill, 4),
            "dsifn_fill": round(mean_ds_fill, 4),
            "adaptformer_hole": round(mean_af_hole, 4),
            "dsifn_hole": round(mean_ds_hole, 4),
            "adaptformer_f1": round(mean_af_f1, 4),
            "dsifn_f1": round(mean_ds_f1, 4),
        },
        "decision": decision,
        "rationale": rationale,
    }
    (OUT / "metrics.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (OUT / "DECISION.md").write_text(
        f"# DSIFN vs AdaptFormer — Thursday decision\n\n"
        f"**Decision: `{decision}`**\n\n"
        f"{rationale}\n\n"
        f"## Means (Delhi {len(delhi_compare)} pairs)\n\n"
        f"| Model | F1 | Fill ratio | Hole rate |\n"
        f"|---|---:|---:|---:|\n"
        f"| AdaptFormer | {mean_af_f1:.3f} | {mean_af_fill:.3f} | {mean_af_hole:.3f} |\n"
        f"| DSIFN (proxy) | {mean_ds_f1:.3f} | {mean_ds_fill:.3f} | {mean_ds_hole:.3f} |\n\n"
        f"## Caveat\n\n"
        f"Official DSIFN pretrained checkpoint was not published in the current Drive zip "
        f"(dataset only). Proxy trained on DSIFN val, evaluated on DSIFN test + Delhi pairs.\n"
        f"Details: `{OUT / 'metrics.json'}`\n",
        encoding="utf-8",
    )
    print(f"\nDECISION: {decision}", flush=True)
    print(rationale, flush=True)
    print(f"Wrote {OUT / 'DECISION.md'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
