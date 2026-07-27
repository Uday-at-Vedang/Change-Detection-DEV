"""
Run detection on full DDA GeoTIFF pairs at native / fullres_tiled resolution.

Default pair: Grid_54.tif vs H43X2E1.tif

Usage:
  # capped high-res (practical on CPU)
  python scripts/run_native_dda_detection.py --max-side 5120

  # true native — uses disk-windowed tiling + low RAM preview (overnight on CPU;
  # much faster if CUDA torch is installed)
  python scripts/run_native_dda_detection.py --native

Outputs under runs/native_dda/<timestamp>/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_BEFORE = ROOT / "data/library_sources/central_delhi/Images/Grid_54.tif"
DEFAULT_AFTER = ROOT / "data/library_sources/central_delhi/Images/H43X2E1.tif"


def _apply_low_ram_native_env() -> None:
    """Bound peak RAM for ~25k GeoTIFFs: stream tiles from disk, tiny preview."""
    os.environ.setdefault("DETECTION_WINDOWED_THRESHOLD", "2048")
    os.environ.setdefault("DETECTION_TILE_MEMORY_MB", "1024")
    os.environ.setdefault("DETECTION_TILE_SIZE", "512")
    os.environ.setdefault("DETECTION_TILE_OVERLAP", "0.35")
    os.environ.setdefault("DETECTION_TILE_BATCH", "1")
    os.environ.setdefault("DETECTION_TTA", "off")
    os.environ.setdefault("DETECTION_MULTISCALE", "off")
    os.environ.setdefault("DETECTION_SKIP_PREBLUR", "true")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", type=str, default=str(DEFAULT_BEFORE))
    ap.add_argument("--after", type=str, default=str(DEFAULT_AFTER))
    ap.add_argument("--max-side", type=int, default=5120,
                    help="Cap for load/working arrays (ignored with --native)")
    ap.add_argument("--native", action="store_true",
                    help="No max-side cap; DETECTION_FULLRES_MAX_SIDE=0 + low-RAM windowed")
    ap.add_argument("--preview-cap", type=int, default=0,
                    help="Preview RGB load cap (default: 2048 native / max-side otherwise)")
    ap.add_argument("--out", type=str, default="")
    args = ap.parse_args()

    before = Path(args.before)
    after = Path(args.after)
    if not before.is_file() or not after.is_file():
        print("Missing before/after GeoTIFF")
        return 1

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)

    os.environ["DETECTION_INFERENCE_MODE"] = "fullres_tiled"
    os.environ["DETECTION_FUSION"] = "dl_only"
    os.environ["DETECTION_SKIP_REGISTRATION_GEOTIFF"] = "true"

    if args.native:
        _apply_low_ram_native_env()
        # GPU: small batch helps; stay conservative on 6GB laptop GPUs
        try:
            import torch
            if torch.cuda.is_available():
                # 6GB laptop GPU: batch=2 is safer than higher
                os.environ["DETECTION_TILE_BATCH"] = os.environ.get(
                    "DETECTION_TILE_BATCH", "2")
                print(f"CUDA device: {torch.cuda.get_device_name(0)}", flush=True)
            else:
                print("CUDA not available — running on CPU (slow)", flush=True)
        except Exception:
            print("torch unavailable for CUDA probe — continuing", flush=True)
        os.environ["DETECTION_FULLRES_MAX_SIDE"] = "0"
        # Canvas / classical size (DL still streams native tiles from disk).
        # Keep max_size = preview so preprocess does not downscale further.
        preview_cap = args.preview_cap or 8192
        max_size = preview_cap
        tag = "native"
    else:
        os.environ["DETECTION_FULLRES_MAX_SIDE"] = str(args.max_side)
        max_size = args.max_side
        tag = f"cap{args.max_side}"
        preview_cap = args.preview_cap or args.max_side

    out = Path(args.out) if args.out else (
        ROOT / "runs/native_dda" / time.strftime("%Y%m%d_%H%M%S") / tag
    )
    out.mkdir(parents=True, exist_ok=True)
    print(f"Output -> {out}", flush=True)
    print(f"Mode=fullres_tiled tag={tag} before={before.name} after={after.name}", flush=True)
    if args.native:
        print(
            f"Native low-RAM: windowed_threshold="
            f"{os.environ.get('DETECTION_WINDOWED_THRESHOLD')} "
            f"tile_mem_mb={os.environ.get('DETECTION_TILE_MEMORY_MB')} "
            f"preview_cap={preview_cap} "
            f"tile_batch={os.environ.get('DETECTION_TILE_BATCH')}",
            flush=True,
        )

    from app.dda.geotiff_io import load_rgb_pil
    from app.detection_engine import run_detection

    t0 = time.time()
    print(f"Loading preview arrays (cap={preview_cap}) for registration/classical...",
          flush=True)
    before_pil = load_rgb_pil(before, max_side=preview_cap)
    after_pil = load_rgb_pil(after, max_side=preview_cap)
    print(f"  loaded {before_pil.size} in {time.time()-t0:.1f}s - starting detection",
          flush=True)

    def _prog(pct, stage):
        print(f"  [{pct:3d}%] {stage}", flush=True)

    mask, vis, stats, regions = run_detection(
        before_pil,
        after_pil,
        method="AI-Based Deep Learning",
        enable_registration=True,
        enable_normalization=True,
        detection_sensitivity=0.5,
        max_size=max_size or preview_cap,
        on_progress=_prog,
        before_path=str(before),
        after_path=str(after),
    )
    elapsed = time.time() - t0
    Image.fromarray(mask if hasattr(mask, "shape") else __import__("numpy").array(mask)).convert("L").save(
        out / "change_mask.png"
    )
    if vis is not None:
        Image.fromarray(vis).save(out / "overlay.png")
    summary = {
        "before": str(before),
        "after": str(after),
        "tag": tag,
        "elapsed_sec": elapsed,
        "stats": {k: v for k, v in (stats or {}).items()
                  if not str(k).startswith("_") and not hasattr(v, "shape")},
        "n_regions": len(regions or []),
        "top_regions": [
            {
                "object_type": r.get("object_type"),
                "area": r.get("area"),
                "confidence": r.get("confidence"),
                "bbox": r.get("bbox"),
            }
            for r in sorted(regions or [], key=lambda x: -x.get("area", 0))[:20]
        ],
    }

    def _safe(o):
        if isinstance(o, dict):
            return {k: _safe(v) for k, v in o.items()
                    if not hasattr(v, "tolist") or True}
        if hasattr(o, "item"):
            try:
                return o.item()
            except Exception:
                return str(o)
        if isinstance(o, (list, tuple)):
            return [_safe(x) for x in o]
        if isinstance(o, (str, int, float, bool)) or o is None:
            return o
        return str(o)

    (out / "summary.json").write_text(json.dumps(_safe(summary), indent=2), encoding="utf-8")
    print(f"Done in {elapsed/60:.1f} min - change%={stats.get('change_percentage')} "
          f"regions={len(regions)} -> {out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
