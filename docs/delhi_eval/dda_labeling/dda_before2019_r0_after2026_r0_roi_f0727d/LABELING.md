# Labeling pack: dda_before2019_r0_after2026_r0_roi_f0727d

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

## Finish the label
1. Edit the mask so **white (255) = true change**, **black (0) = no change**.
2. Save as `gt_mask.png` in this folder (single-channel or RGB white/black).

## Ingest into the eval set
```bash
python scripts/ingest_dda_gt_label.py --pair-id dda_before2019_r0_after2026_r0_roi_f0727d
python scripts/build_delhi_cd_splits.py
```
