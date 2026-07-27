# Labeling pack: Grid_54 vs H43X2E1

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
This copies `gt_mask.png` → `docs/delhi_eval/labels/dda_grid54_h43x2e1.png` and updates
the manifest. Calibration / fine-tune scripts will then use it.

## Notes
- Preview size is downscaled from native ~25k×28k for tractable labeling.
- After ingest, re-run calibration:
  `python scripts/v3_error_analysis_and_thr_sweep.py`
  `python scripts/grid_search_calibration.py --methods "AI-Based Deep Learning" --sensitivities 0.4,0.5,0.6 --pair-ids dda_grid54_h43x2e1`
