"""Environment check + AdaptFormer eval on the real Delhi seed GT.

Reports overall F1/P/R/IoU and condition buckets (vegetation, roof, lighting,
alignment) wherever labels exist. Uses the same run_detection + binary_metrics
path as scripts/evaluate_finetuned_vs_baseline.py.

Usage (from change_detection_webapp):
  python -u scripts/eval_seed_conditions.py
  python -u scripts/eval_seed_conditions.py --weights models/adaptformer_delhi/wed_retrain
"""
from __future__ import annotations

import argparse
import json
import os
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

from app.evaluation.delhi_eval import _load_label, _load_rgb, load_manifest  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402

DEFAULT_WEIGHTS = (
    ROOT / "runs" / "finetune_adaptformer" / "priyanka_gt" / "20260903_163459" / "best"
)
SYNTHETIC_CANDIDATES = (
    Path(r"C:\Users\Priyanka\Downloads\Synthetic_CD_dataset"),
    Path(r"C:\Users\udayb\Downloads\Synthetic_CD_dataset"),
    ROOT / "data" / "synthetic_cd",
    ROOT / "data" / "Synthetic_CD_dataset",
)
HELD_OUT_EMPTY = {
    "delhi_0002", "delhi_0006", "delhi_0007", "delhi_0008",
    "delhi_0010", "delhi_0013", "delhi_0014", "delhi_0015",
}
ROOF_PAIRS = {"dda_before5_after5_v2", "dda_before6_after6_v2"}


def _resolve_pair_file(rel: str, pair_id: str, kind: str) -> Path | None:
    raw = Path(rel) if rel else Path()
    candidates: list[Path] = []
    if rel:
        if raw.is_absolute():
            candidates.append(raw)
            candidates.append(Path(r"C:\Users\udayb\Downloads") / raw.name)
            candidates.append(Path(r"C:\Users\udayb\Downloads") / raw.name.replace(" (1)", ""))
        else:
            candidates.append(ROOT / raw)
    pack = ROOT / "docs" / "delhi_eval" / "dda_labeling" / pair_id
    kind_name = {"before": "before.png", "after": "after.png", "gt": "gt_mask.png"}[kind]
    candidates.append(pack / kind_name)
    if kind == "gt":
        candidates.append(ROOT / "docs" / "delhi_eval" / "labels" / f"{pair_id}.png")
    for path in candidates:
        if path.is_file():
            return path
    return None


def discover_synthetic_dir() -> Path | None:
    for path in SYNTHETIC_CANDIDATES:
        if (path / "before").is_dir() and (path / "after").is_dir() and (path / "mask").is_dir():
            return path
    return None


def _summarize(rows: list[dict]) -> dict | None:
    if not rows:
        return None
    tp = sum(r["tp"] for r in rows)
    fp = sum(r["fp"] for r in rows)
    fn = sum(r["fn"] for r in rows)
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 1.0
    iou = tp / (tp + fp + fn) if (tp + fp + fn) else 1.0
    return {
        "n": len(rows),
        "mean_f1": round(float(np.mean([r["f1"] for r in rows])), 4),
        "mean_precision": round(float(np.mean([r["precision"] for r in rows])), 4),
        "mean_recall": round(float(np.mean([r["recall"] for r in rows])), 4),
        "mean_iou": round(float(np.mean([r["iou"] for r in rows])), 4),
        "mean_pixel_acc": round(float(np.mean([r["pixelAccuracy"] for r in rows])), 4),
        "micro_f1": round(f1, 4),
        "micro_precision": round(prec, 4),
        "micro_recall": round(rec, 4),
        "micro_iou": round(iou, 4),
        "pair_ids": [r["pair_id"] for r in rows],
    }


def env_check(weights: Path) -> dict:
    import torch
    from app.model_inference import get_model_status, predict_change_mask, preload_model

    info = {
        "torch": torch.__version__,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_version": torch.version.cuda,
        "device_name": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        "capability": list(torch.cuda.get_device_capability(0)) if torch.cuda.is_available() else None,
        "weights": str(weights),
        "weights_exist": weights.is_dir() and (weights / "model.safetensors").is_file(),
    }
    rng = np.random.default_rng(0)
    a = rng.integers(40, 200, (256, 256, 3), dtype=np.uint8)
    b = a.copy()
    b[80:140, 90:160] = [210, 200, 190]
    t0 = time.time()
    ok = bool(preload_model())
    mask, score = predict_change_mask(a, b)
    status = get_model_status()
    info.update({
        "preload_ok": ok,
        "forward_s": round(time.time() - t0, 2),
        "mask_shape": list(mask.shape),
        "score_min": round(float(np.min(score)), 4),
        "score_max": round(float(np.max(score)), 4),
        "pred_change_pct": round(100.0 * float((mask > 127).mean()), 3),
        "adaptformer": {
            "available": status.get("available"),
            "loadedFrom": status.get("loadedFrom"),
            "device": status.get("device"),
            "detectionMode": status.get("detectionMode"),
            "calibratedThreshold": status.get("calibratedThreshold"),
            "error": status.get("error"),
        },
    })
    return info


def pair_conditions(pair: dict, gt_frac: float, ncc: float | None) -> list[str]:
    types = {str(t).lower() for t in (pair.get("change_types") or [])}
    pid = pair["pair_id"]
    notes = (pair.get("notes") or "").lower()
    conds: list[str] = []
    if "vegetation" in types:
        conds.append("vegetation")
    if pid in ROOF_PAIRS:
        conds.append("roof")
    if pid in HELD_OUT_EMPTY or "seasonal" in notes or "crop-texture" in notes:
        conds.append("lighting")
    if pid in ROOF_PAIRS or ncc is not None:
        conds.append("alignment")
    if gt_frac < 0.001 and pid in HELD_OUT_EMPTY:
        # empty held-out pairs are the real-imagery lighting/seasonal FP gate
        if "lighting" not in conds:
            conds.append("lighting")
    return conds


def eval_one(pair: dict, method: str) -> dict:
    from app.detection_engine import run_detection

    pid = pair["pair_id"]
    before_p = _resolve_pair_file(pair.get("before_path") or "", pid, "before")
    after_p = _resolve_pair_file(pair.get("after_path") or "", pid, "after")
    gt_p = _resolve_pair_file(pair.get("gt_mask") or "", pid, "gt")
    if not before_p or not after_p or not gt_p:
        return {"pair_id": pid, "error": "missing_files",
                "before": str(before_p), "after": str(after_p), "gt": str(gt_p)}

    before = _load_rgb(before_p)
    after = _load_rgb(after_p)
    gt = _load_label(gt_p)
    meta_path = ROOT / "docs" / "delhi_eval" / "dda_labeling" / pid / "meta.json"
    ncc = None
    aligned = None
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ncc = meta.get("ncc")
        aligned = meta.get("aligned")

    tif_b = str(before_p) if before_p.suffix.lower() in {".tif", ".tiff"} else None
    tif_a = str(after_p) if after_p.suffix.lower() in {".tif", ".tiff"} else None
    t0 = time.time()
    mask, _img, stats, regions = run_detection(
        Image.fromarray(before), Image.fromarray(after),
        method=method,
        enable_registration=True, enable_normalization=True,
        detection_sensitivity=0.5,
        before_path=tif_b, after_path=tif_a,
    )
    if mask.shape != gt.shape:
        import cv2
        mask = cv2.resize(mask, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_NEAREST)
    m = binary_metrics(mask, gt)
    counts = m["counts"]
    gt_frac = float((gt > 127).mean()) if gt.max() > 1 else float((gt > 0).mean())
    params = stats.get("params") or {}
    row = {
        "pair_id": pid,
        "f1": m["f1"],
        "precision": m["precision"],
        "recall": m["recall"],
        "iou": m["iou"],
        "pixelAccuracy": m["pixelAccuracy"],
        "falsePositiveRate": m["falsePositiveRate"],
        "tp": counts["tp"], "fp": counts["fp"], "fn": counts["fn"], "tn": counts["tn"],
        "gt_change_frac": round(gt_frac, 6),
        "pred_change_pct": round(float(stats.get("change_percentage") or 0.0), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "n_regions": len(regions or []),
        "change_types": list(pair.get("change_types") or []),
        "split": pair.get("_split", "unspecified"),
        "ncc": ncc,
        "aligned": aligned,
        "registration_ok": params.get("registration_ok"),
        "alignment_warning": bool(stats.get("alignment_warning") or params.get("alignment_warning")),
        "conditions": pair_conditions(pair, gt_frac, ncc),
    }
    return row


def _base_scene(size=384, seed=0):
    rng = np.random.default_rng(seed)
    img = rng.integers(40, 200, (size, size, 3), dtype=np.uint8)
    img[:, size // 3: size // 3 + 6] = [90, 90, 90]
    img[size // 2: size // 2 + 6, :] = [110, 100, 80]
    return img


def synthetic_condition_cases():
    size = 384
    before = _base_scene(size, seed=1)
    after = before.copy()
    gt = np.zeros((size, size), dtype=np.uint8)
    for (x, y, w, h) in [(60, 70, 50, 40), (220, 90, 60, 55), (150, 250, 70, 45)]:
        after[y:y + h, x:x + w] = [205, 200, 190]
        gt[y:y + h, x:x + w] = 255
    yield before, after, gt, "inserted_buildings", "roof"

    before = _base_scene(size, seed=2)
    after = np.clip(before.astype(np.float32) * 1.18 + 12, 0, 255).astype(np.uint8)
    gt = np.zeros((size, size), dtype=np.uint8)
    yield before, after, gt, "brightness_only", "lighting"

    before = _base_scene(size, seed=3)
    shifted = np.roll(np.roll(before, 6, axis=0), 4, axis=1)
    after = shifted.copy()
    gt = np.zeros((size, size), dtype=np.uint8)
    x, y, w, h = 180, 160, 80, 60
    after[y:y + h, x:x + w] = [210, 60, 60]
    gt[y:y + h, x:x + w] = 255
    yield before, after, gt, "misaligned_change", "alignment"

    before = _base_scene(size, seed=5)
    after = before.copy()
    gt = np.zeros((size, size), dtype=np.uint8)
    before[40:140, 50:180] = [35, 120, 45]
    after[40:140, 50:180] = [150, 130, 90]
    gt[40:140, 50:180] = 255
    yield before, after, gt, "vegetation_cleared", "vegetation"


def eval_synthetic_conditions(method: str) -> list[dict]:
    from app.detection_engine import run_detection

    rows = []
    for before, after, gt, name, condition in synthetic_condition_cases():
        t0 = time.time()
        mask, _img, stats, regions = run_detection(
            Image.fromarray(before), Image.fromarray(after),
            method=method,
            enable_registration=True, enable_normalization=True,
            detection_sensitivity=0.5,
        )
        if mask.shape != gt.shape:
            import cv2
            mask = cv2.resize(mask, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_NEAREST)
        m = binary_metrics(mask, gt)
        counts = m["counts"]
        rows.append({
            "pair_id": name,
            "condition": condition,
            "f1": m["f1"], "precision": m["precision"], "recall": m["recall"],
            "iou": m["iou"], "pixelAccuracy": m["pixelAccuracy"],
            "falsePositiveRate": m["falsePositiveRate"],
            "tp": counts["tp"], "fp": counts["fp"], "fn": counts["fn"], "tn": counts["tn"],
            "elapsed_s": round(time.time() - t0, 2),
            "n_regions": len(regions or []),
            "pred_change_pct": round(float(stats.get("change_percentage") or 0.0), 4),
        })
        print(f"  [synth/{condition}] {name}: F1={m['f1']:.3f} IoU={m['iou']:.3f}", flush=True)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", default=str(DEFAULT_WEIGHTS))
    parser.add_argument("--method", default="AI-Based Deep Learning")
    parser.add_argument("--out", default="runs/eval_seed_conditions/report.json")
    args = parser.parse_args()

    weights = Path(args.weights)
    if not weights.is_absolute():
        weights = ROOT / weights
    os.environ["ADAPTFORMER_WEIGHTS"] = str(weights)
    thr_path = weights / "threshold.json"
    if thr_path.is_file():
        sidecar = json.loads(thr_path.read_text(encoding="utf-8"))
        os.environ["ADAPTFORMER_THRESHOLD"] = str(sidecar["threshold"])

    print("=== 1. Environment check ===", flush=True)
    env = env_check(weights)
    print(json.dumps(env, indent=2), flush=True)
    if not env.get("cuda_available"):
        print("WARNING: CUDA not available — eval will be slow/CPU", flush=True)
    if not env.get("adaptformer", {}).get("available"):
        raise SystemExit("AdaptFormer failed to load; aborting eval")

    synth_dir = discover_synthetic_dir()
    print("\n=== 2. Synthetic dataset ===", flush=True)
    print(f"  found: {synth_dir}" if synth_dir else "  NOT FOUND — skip fine-tune", flush=True)

    print("\n=== 3. Real seed evaluation ===", flush=True)
    manifest = load_manifest()
    split_path = ROOT / "data" / "delhi_cd" / "split.json"
    split_ids = {"train": set(), "val": set(), "test": set()}
    if split_path.is_file():
        split = json.loads(split_path.read_text(encoding="utf-8"))
        for key in ("train", "val", "test"):
            split_ids[key] = set(split.get(key) or [])
    test_ids = set(split_ids["test"])
    if (ROOT / "data" / "delhi_cd" / "test" / "manifest.json").is_file():
        test_man = json.loads((ROOT / "data" / "delhi_cd" / "test" / "manifest.json").read_text(encoding="utf-8"))
        test_ids = {p["pair_id"] for p in test_man.get("pairs", [])}

    rows = []
    skipped = []
    for pair in manifest.get("pairs", []):
        pid = pair.get("pair_id") or ""
        if pid in split_ids["train"]:
            pair["_split"] = "train"
        elif pid in split_ids["val"]:
            pair["_split"] = "val"
        elif pid in test_ids:
            pair["_split"] = "test"
        elif pid in HELD_OUT_EMPTY:
            pair["_split"] = "heldout_empty"
        else:
            pair["_split"] = "other"
        print(f"  eval {pid} ...", flush=True)
        row = eval_one(pair, args.method)
        if row.get("error"):
            skipped.append(row)
            print(f"    SKIP {row['error']}", flush=True)
            continue
        rows.append(row)
        print(
            f"    F1={row['f1']:.3f} P={row['precision']:.3f} R={row['recall']:.3f} "
            f"IoU={row['iou']:.3f} cond={row['conditions']} split={row['split']}",
            flush=True,
        )

    print("\n=== 3b. Synthetic condition probes ===", flush=True)
    synth_rows = eval_synthetic_conditions(args.method)

    change_pos = [r for r in rows if r["gt_change_frac"] >= 0.001]
    empty = [r for r in rows if r["gt_change_frac"] < 0.001]
    buckets = {}
    for name in ("vegetation", "roof", "lighting", "alignment"):
        buckets[name] = _summarize([r for r in rows if name in r["conditions"]])
        buckets[f"{name}_change_positive"] = _summarize(
            [r for r in rows if name in r["conditions"] and r["gt_change_frac"] >= 0.001]
        )

    report = {
        "created_unix": time.time(),
        "weights": str(weights),
        "threshold": os.environ.get("ADAPTFORMER_THRESHOLD"),
        "method": args.method,
        "synthetic_dir": str(synth_dir) if synth_dir else None,
        "env": env,
        "overall_all_labeled": _summarize(rows),
        "overall_change_positive": _summarize(change_pos),
        "overall_empty_gt": _summarize(empty),
        "frozen_test": _summarize([r for r in rows if r["pair_id"] in test_ids]),
        "heldout_empty_fp": _summarize([r for r in rows if r["pair_id"] in HELD_OUT_EMPTY]),
        "conditions": buckets,
        "synthetic_probes": synth_rows,
        "skipped": skipped,
        "pairs": rows,
    }
    out = ROOT / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\n=== Summary ===", flush=True)
    for key in ("overall_all_labeled", "overall_change_positive", "frozen_test", "heldout_empty_fp"):
        s = report[key]
        if not s:
            print(f"  {key}: n/a", flush=True)
            continue
        print(
            f"  {key}: n={s['n']} mean_F1={s['mean_f1']:.4f} P={s['mean_precision']:.4f} "
            f"R={s['mean_recall']:.4f} IoU={s['mean_iou']:.4f} micro_F1={s['micro_f1']:.4f}",
            flush=True,
        )
    for name in ("vegetation", "roof", "lighting", "alignment"):
        s = buckets.get(name)
        print(f"  {name}: {s}" if s else f"  {name}: n/a", flush=True)
    print(f"Wrote {out}", flush=True)
    print("EVAL_DONE", flush=True)


if __name__ == "__main__":
    main()
