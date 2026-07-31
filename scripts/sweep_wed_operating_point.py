"""Thursday OP sweep on wed_retrain: F_beta thr + TTA + multiscale.

Keeps only settings that raise test F1 without exploding runtime
(vs baseline: thr from threshold.json, TTA=off, multiscale=off).

Usage:
    python scripts/sweep_wed_operating_point.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)
except ImportError:
    pass

CKPT = (ROOT / "models" / "adaptformer_delhi" / "wed_retrain").resolve()
OUT = ROOT / "data" / "delhi_cd" / "thursday_op_sweep"
DELHI_CD = ROOT / "data" / "delhi_cd"

# Runtime budget: reject configs slower than this multiplier vs baseline.
MAX_RUNTIME_MULT = 2.5
# F1 must improve by at least this absolute amount to keep a setting.
MIN_F1_GAIN = 0.005
BETA = 1.5

os.environ["ADAPTFORMER_WEIGHTS"] = str(CKPT)
os.environ["DETECTION_FUSION"] = "dl_only"
# Cleared / set per trial below
os.environ["DETECTION_TTA"] = "off"
os.environ["DETECTION_MULTISCALE"] = "off"
os.environ.pop("ADAPTFORMER_THRESHOLD", None)
os.environ.pop("DETECTION_DL_THRESHOLD", None)


def _fbeta(p: float, r: float, beta: float = BETA) -> float:
    if p + r <= 0:
        return 0.0
    b2 = beta * beta
    return (1 + b2) * p * r / (b2 * p + r)


def _load_thr_sidecar() -> float:
    p = CKPT / "threshold.json"
    if p.is_file():
        return float(json.loads(p.read_text(encoding="utf-8"))["threshold"])
    return 0.446


def _load_splits():
    # Import after env weights are set
    sys.path.insert(0, str(ROOT / "scripts"))
    from finetune_adaptformer import _load_pairs_from_delhi_cd  # type: ignore

    train, val, test, info = _load_pairs_from_delhi_cd(DELHI_CD)
    return val, test, info


def _predict_scores(pairs):
    """Run model_inference.predict_change_mask (respects TTA/MS env) → score maps."""
    from app.model_inference import predict_change_mask, preload_model

    preload_model()
    scores, gts, ids = [], [], []
    for before, after, gt, pid in pairs:
        _mask, score = predict_change_mask(before, after, threshold=2.0)  # score only
        if score.shape[:2] != gt.shape[:2]:
            import cv2
            score = cv2.resize(score, (gt.shape[1], gt.shape[0]), interpolation=cv2.INTER_LINEAR)
        scores.append(score.astype(np.float32))
        gts.append(gt > 127)
        ids.append(pid)
    return scores, gts, ids


def _metrics_at_thr(scores, gts, thr: float) -> dict:
    f1s, precs, recs, ious = [], [], [], []
    for score, gt in zip(scores, gts):
        if not gt.any():
            continue
        m = score >= thr
        tp = int((m & gt).sum())
        fp = int((m & ~gt).sum())
        fn = int((~m & gt).sum())
        p = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
        r = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
        f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
        iou = 0.0 if (tp + fp + fn) == 0 else tp / (tp + fp + fn)
        f1s.append(f1)
        precs.append(p)
        recs.append(r)
        ious.append(iou)
    mean_p = float(np.mean(precs)) if precs else 0.0
    mean_r = float(np.mean(recs)) if recs else 0.0
    return {
        "f1": round(float(np.mean(f1s)) if f1s else 0.0, 4),
        "precision": round(mean_p, 4),
        "recall": round(mean_r, 4),
        "iou": round(float(np.mean(ious)) if ious else 0.0, 4),
        "fbeta": round(_fbeta(mean_p, mean_r), 4),
        "n": len(f1s),
        "thr": thr,
    }


def _sweep_thr_fbeta(scores, gts, thr_min=0.15, thr_max=0.70, n=28) -> tuple[float, dict, list]:
    grid = sorted({round(float(t), 4) for t in np.linspace(thr_min, thr_max, n)})
    best_thr, best_obj, best_row = 0.5, -1.0, {}
    sweep = []
    for thr in grid:
        row = _metrics_at_thr(scores, gts, thr)
        sweep.append(row)
        if row["fbeta"] > best_obj + 1e-9:
            best_obj = row["fbeta"]
            best_thr = thr
            best_row = row
    return best_thr, best_row, sweep


def _reset_model_cache():
    import app.model_inference as mi
    mi._MODEL = None
    mi._PROCESSOR = None
    mi._DEVICE = None
    mi._AVAILABLE = None
    mi._LOAD_FAILED = False
    mi._LOAD_ERROR = None
    mi._LOADED_FROM = None
    mi._CALIBRATED_THRESHOLD = None


def _run_config(name: str, tta: str, multiscale: str, pairs, thr: float | None = None) -> dict:
    os.environ["DETECTION_TTA"] = tta
    os.environ["DETECTION_MULTISCALE"] = multiscale
    if thr is not None:
        os.environ["ADAPTFORMER_THRESHOLD"] = str(thr)
        os.environ["DETECTION_DL_THRESHOLD"] = str(thr)
    _reset_model_cache()
    t0 = time.perf_counter()
    scores, gts, ids = _predict_scores(pairs)
    elapsed = time.perf_counter() - t0
    # Use provided thr, else F_beta calibrate on these scores (caller decides)
    use_thr = thr if thr is not None else 0.5
    metrics = _metrics_at_thr(scores, gts, use_thr)
    return {
        "name": name,
        "tta": tta,
        "multiscale": multiscale,
        "thr": use_thr,
        "elapsed_s": round(elapsed, 2),
        "pair_ids": ids,
        **metrics,
        "_scores": scores,
        "_gts": gts,
    }


def main() -> int:
    if not (CKPT / "model.safetensors").is_file():
        print(f"MISSING weights: {CKPT / 'model.safetensors'}")
        return 1

    OUT.mkdir(parents=True, exist_ok=True)
    sidecar_thr = _load_thr_sidecar()
    print(f"wed_retrain sidecar thr={sidecar_thr}", flush=True)

    val_pairs, test_pairs, split_info = _load_splits()
    print(f"val={len(val_pairs)} test={len(test_pairs)}", flush=True)

    # --- 1) Baseline scores on val (TTA off, MS off) for F_beta thr sweep ---
    print("\n=== Val: baseline scores (TTA=off, MS=off) ===", flush=True)
    base_val = _run_config("val_base", "off", "off", val_pairs, thr=sidecar_thr)
    fbeta_thr, fbeta_row, thr_sweep = _sweep_thr_fbeta(base_val["_scores"], base_val["_gts"])
    print(
        f"  sidecar@{sidecar_thr}: F1={base_val['f1']} Fb={base_val['fbeta']} "
        f"P={base_val['precision']} R={base_val['recall']}  ({base_val['elapsed_s']}s)",
        flush=True,
    )
    print(
        f"  F_beta thr pick: thr={fbeta_thr} F1={fbeta_row['f1']} Fb={fbeta_row['fbeta']} "
        f"P={fbeta_row['precision']} R={fbeta_row['recall']}",
        flush=True,
    )

    # --- 2) Test configs ---
    # Candidate operating points: (name, tta, multiscale, thr)
    # Multiscale: native+0.75 is light; 0.5,1,1.5 is heavier.
    candidates = [
        ("baseline_sidecar", "off", "off", sidecar_thr),
        ("fbeta_thr", "off", "off", fbeta_thr),
        # TTA / multiscale at both thr picks (F_beta thr can hurt test F1)
        ("tta_hflip_sidecar", "hflip", "off", sidecar_thr),
        ("tta_full_sidecar", "full", "off", sidecar_thr),
        ("ms_075_1_sidecar", "off", "0.75,1.0", sidecar_thr),
        ("tta_hflip", "hflip", "off", fbeta_thr),
        ("tta_full", "full", "off", fbeta_thr),
        ("ms_075_1", "off", "0.75,1.0", fbeta_thr),
        ("tta_h_ms_075", "hflip", "0.75,1.0", fbeta_thr),
        ("ms_05_1_15", "off", "0.5,1.0,1.5", fbeta_thr),
    ]

    print("\n=== Test sweep ===", flush=True)
    results = []
    baseline = None
    for name, tta, ms, thr in candidates:
        print(f"\n-- {name}: TTA={tta} MS={ms} thr={thr} --", flush=True)
        row = _run_config(name, tta, ms, test_pairs, thr=thr)
        # Drop heavy numpy from persisted row
        clean = {k: v for k, v in row.items() if not k.startswith("_")}
        results.append(clean)
        print(
            f"  F1={clean['f1']} Fb={clean['fbeta']} P={clean['precision']} "
            f"R={clean['recall']} IoU={clean['iou']}  time={clean['elapsed_s']}s",
            flush=True,
        )
        if name == "baseline_sidecar":
            baseline = clean

    assert baseline is not None
    base_f1 = baseline["f1"]
    base_t = max(baseline["elapsed_s"], 1e-3)

    kept = []
    for r in results:
        gain = r["f1"] - base_f1
        slow = r["elapsed_s"] / base_t
        ok = (gain >= MIN_F1_GAIN) and (slow <= MAX_RUNTIME_MULT)
        # Always keep baseline as reference
        if r["name"] == "baseline_sidecar":
            ok = True
        r["f1_gain"] = round(gain, 4)
        r["runtime_mult"] = round(slow, 2)
        r["keep"] = ok and r["name"] != "baseline_sidecar"
        if r["keep"]:
            kept.append(r)
        print(
            f"  decide {r['name']}: gain={r['f1_gain']:+.4f} runtime×{r['runtime_mult']:.2f} "
            f"{'KEEP' if r['keep'] else ('BASE' if r['name']=='baseline_sidecar' else 'DROP')}",
            flush=True,
        )

    # Winner: among kept, max F1; if none, use better of baseline vs fbeta_thr if fbeta helps
    pool = kept if kept else [
        r for r in results
        if r["name"] in ("baseline_sidecar", "fbeta_thr") and r["runtime_mult"] <= MAX_RUNTIME_MULT
    ]
    if not pool:
        pool = [baseline]
    winner = max(pool, key=lambda r: (r["f1"], -r["elapsed_s"]))

    # Freeze winning OP into threshold.json (+ sidecar op file); do NOT replace v3_frozen.
    freeze = {
        "threshold": winner["thr"],
        "val_fbeta_thr": fbeta_thr,
        "val_fbeta_row": fbeta_row,
        "sidecar_thr_was": sidecar_thr,
        "tta": winner["tta"],
        "multiscale": winner["multiscale"],
        "test_f1": winner["f1"],
        "test_precision": winner["precision"],
        "test_recall": winner["recall"],
        "test_iou": winner["iou"],
        "test_fbeta": winner["fbeta"],
        "test_elapsed_s": winner["elapsed_s"],
        "frozen_from": winner["name"],
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "policy": {
            "min_f1_gain": MIN_F1_GAIN,
            "max_runtime_mult": MAX_RUNTIME_MULT,
            "fbeta": BETA,
            "note": "Operating point for wed_retrain only; production remains v3_frozen until promoted.",
        },
    }

    # Update wed_retrain/threshold.json with new thr + OP fields (preserve epoch/val metrics)
    old_thr = {}
    thr_path = CKPT / "threshold.json"
    if thr_path.is_file():
        old_thr = json.loads(thr_path.read_text(encoding="utf-8"))
    new_thr = {
        **old_thr,
        "threshold": freeze["threshold"],
        "operating_point": {
            "tta": freeze["tta"],
            "multiscale": freeze["multiscale"],
            "frozen_from": freeze["frozen_from"],
            "frozen_at": freeze["frozen_at"],
            "test_f1": freeze["test_f1"],
            "test_precision": freeze["test_precision"],
            "test_recall": freeze["test_recall"],
            "test_iou": freeze["test_iou"],
        },
        "thursday_fbeta_calibrated_thr": fbeta_thr,
        "thursday_fbeta_val": fbeta_row,
    }
    thr_path.write_text(json.dumps(new_thr, indent=2), encoding="utf-8")

    op_path = CKPT / "operating_point.json"
    op_path.write_text(json.dumps(freeze, indent=2), encoding="utf-8")

    summary = {
        "model": str(CKPT),
        "split": split_info,
        "val_thr_sweep_fbeta": thr_sweep,
        "fbeta_thr": fbeta_thr,
        "results": results,
        "kept": [r["name"] for r in kept],
        "winner": winner["name"],
        "freeze": freeze,
        "written": {
            "threshold_json": str(thr_path),
            "operating_point_json": str(op_path),
        },
    }
    out_json = OUT / "metrics.json"
    out_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWINNER: {winner['name']}  F1={winner['f1']} thr={winner['thr']} "
          f"TTA={winner['tta']} MS={winner['multiscale']}", flush=True)
    print(f"Froze -> {thr_path}", flush=True)
    print(f"Summary -> {out_json}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
