"""
Prepare hand-labeling assets for the DDA pair Grid_54.tif vs H43X2E1.tif.

Creates a paint/QGIS-friendly pack under ``docs/delhi_eval/dda_labeling/``:
  - before.png / after.png   (downscaled RGB, default max_side=4096)
  - seed_mask.png            (current AdaptFormer+recovery detection as a draft)
  - gt_mask_blank.png        (empty mask — paint change as white on black)
  - LABELING.md              (how to finish the label and ingest it)
  - meta.json

Also registers the pair in ``docs/delhi_eval/manifest.json`` as ``dda_grid54_h43x2e1``.

Usage (from repo root):
  python scripts/prepare_dda_gt_labeling.py
  python scripts/prepare_dda_gt_labeling.py --max-side 3072 --skip-seed
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BEFORE = ROOT / "data/library_sources/central_delhi/Images/Grid_54.tif"
AFTER = ROOT / "data/library_sources/central_delhi/Images/H43X2E1.tif"
OUT_DIR = ROOT / "docs/delhi_eval/dda_labeling/dda_grid54_h43x2e1"
PAIR_ID = "dda_grid54_h43x2e1"
MANIFEST = ROOT / "docs/delhi_eval/manifest.json"
LABEL_PATH = ROOT / "docs/delhi_eval/labels" / f"{PAIR_ID}.png"


def _load_rgb(path: Path, max_side: int) -> np.ndarray:
    from app.dda.geotiff_io import load_rgb_pil
    return np.array(load_rgb_pil(path, max_side=max_side).convert("RGB"))


def _register_manifest(preview_w: int, preview_h: int) -> None:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    pairs = data.get("pairs") or []
    entry = {
        "pair_id": PAIR_ID,
        "before_path": str(BEFORE.relative_to(ROOT)).replace("\\", "/"),
        "after_path": str(AFTER.relative_to(ROOT)).replace("\\", "/"),
        "date_before": None,
        "date_after": None,
        "gsd": 0.03,
        "zone": "Central Delhi / Grid 54",
        "change_types": ["building", "road", "vegetation", "mixed_gsd"],
        "gt_mask": None,
        "notes": (
            "DDA production pair (Grid_54 vs H43X2E1). Hand-label in "
            f"docs/delhi_eval/dda_labeling/{PAIR_ID}/ then run "
            "scripts/ingest_dda_gt_label.py"
        ),
        "label_preview_size": [preview_w, preview_h],
    }
    # replace if exists
    pairs = [p for p in pairs if p.get("pair_id") != PAIR_ID]
    pairs.append(entry)
    data["pairs"] = pairs
    MANIFEST.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"Registered {PAIR_ID} in manifest ({len(pairs)} pairs total)")


def _write_guide(out: Path) -> None:
    text = f"""# Labeling pack: Grid_54 vs H43X2E1

## Goal
Hand-draw **real permanent ground change** (new buildings, demolition, roads).
Do **not** mark cars, shadows, seasonal tree canopy, or illumination shifts.

## Files
| File | Use |
|---|---|
| `before.png` | earlier date |
| `after.png` | later date |
| `seed_mask.png` | draft from current detector (white=change) — **edit this** |
| `gt_mask_blank.png` | empty alternative if you prefer starting from scratch |

## Paint / Photoshop
1. Open `after.png` and `seed_mask.png`.
2. Edit the mask so **white (255) = true change**, **black (0) = no change**.
3. Save as `gt_mask.png` in this folder (single-channel or RGB white/black).

## QGIS
1. Load `before.png` / `after.png` as rasters (same extent).
2. Digitize change polygons → rasterize to same size as the PNGs → export
   binary GeoTIFF/PNG as `gt_mask.png`.

## Ingest into the eval set
```bash
python scripts/ingest_dda_gt_label.py
```
This copies `gt_mask.png` → `docs/delhi_eval/labels/{PAIR_ID}.png` and updates
the manifest. Calibration / fine-tune scripts will then use it.

## Notes
- Preview size is downscaled from native ~25k×28k for tractable labeling.
- After ingest, re-run calibration:
  `python scripts/v3_error_analysis_and_thr_sweep.py`
  `python scripts/grid_search_calibration.py --methods "AI-Based Deep Learning" --sensitivities 0.4,0.5,0.6 --pair-ids {PAIR_ID}`
"""
    (out / "LABELING.md").write_text(text, encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-side", type=int, default=4096)
    ap.add_argument("--skip-seed", action="store_true",
                    help="Skip AdaptFormer seed mask (faster pack-only)")
    args = ap.parse_args()

    if not BEFORE.is_file() or not AFTER.is_file():
        print("Missing Grid_54.tif or H43X2E1.tif under data/library_sources/...")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Loading pair at max_side={args.max_side} ...")
    t0 = time.time()
    before = _load_rgb(BEFORE, args.max_side)
    after = _load_rgb(AFTER, args.max_side)
    print(f"  loaded {before.shape} in {time.time()-t0:.1f}s")

    Image.fromarray(before).save(OUT_DIR / "before.png")
    Image.fromarray(after).save(OUT_DIR / "after.png")
    blank = np.zeros(before.shape[:2], dtype=np.uint8)
    Image.fromarray(blank).save(OUT_DIR / "gt_mask_blank.png")

    seed_px = 0
    if not args.skip_seed:
        print("Running detection for seed mask (this can take several minutes)...")
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=True)
        from app.detection_engine import run_detection
        mask, _vis, stats, regions = run_detection(
            Image.fromarray(before),
            Image.fromarray(after),
            method="AI-Based Deep Learning",
            enable_registration=True,
            enable_normalization=True,
            detection_sensitivity=0.5,
            max_size=args.max_side,
            before_path=str(BEFORE),
            after_path=str(AFTER),
        )
        seed = np.array(mask)
        if seed.ndim == 3:
            seed = seed[:, :, 0]
        Image.fromarray(seed.astype(np.uint8)).save(OUT_DIR / "seed_mask.png")
        seed_px = int((seed > 127).sum())
        print(f"  seed change%={stats.get('change_percentage')} regions={len(regions)} px={seed_px}")
    else:
        Image.fromarray(blank).save(OUT_DIR / "seed_mask.png")

    meta = {
        "pair_id": PAIR_ID,
        "before": str(BEFORE),
        "after": str(AFTER),
        "preview_shape": list(before.shape[:2]),
        "max_side": args.max_side,
        "seed_changed_px": seed_px,
        "created_unix": time.time(),
    }
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    _write_guide(OUT_DIR)
    _register_manifest(before.shape[1], before.shape[0])
    print(f"Labeling pack ready: {OUT_DIR}")
    print("Next: edit seed_mask.png → save as gt_mask.png → run scripts/ingest_dda_gt_label.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
