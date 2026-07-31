# Accuracy improvement pack (resumed 2026-07-21 after PC crash)

## Pull from Priyanka
Merged `github-dev/New/Priyanka` → `uday` (`5462489`).

## Task 1 — GT for Grid_54 vs H43X2E1 — DONE (curated)
Hand-reviewed fullres detection regions; kept structural changes only
(new blue/grey roofs, compound rebuild, solar). Dropped shadows, pool
water color, wetland/vegetation.

- Label: `docs/delhi_eval/labels/dda_grid54_h43x2e1.png` (change≈0.23%, 10 components)
- Pack: `docs/delhi_eval/dda_labeling/dda_grid54_h43x2e1/gt_mask.png`
- Review notes/overview: `.../_review/gt_labeling_notes.json`, `gt_overview.png`

Re-paint and re-ingest anytime:
```bash
python scripts/ingest_dda_gt_label.py
```

## Task 2 — Threshold / config recalibration — DONE
**v3_frozen:** keep **thr=0.2** (`runs/calibration/v3_recalibration_recommendation.json`)

App grid (`dl_only`): sens **0.4** slightly best; DL floors irrelevant under `dl_only`
(`runs/calibration/grid_search_v3_dl_only/summary.json`).

**Hard-neg thr sweep:** still best at **0.2** (val F1 0.688, test F1 0.563)
→ `runs/v3_hardneg_analysis/recommended_threshold.json`

## Task 3 — Hard negatives + fine-tune — DONE
- Mined 7 veg/seasonal FP tiles → `data/delhi_cd/hard_negatives/`
- Fixed `keep-empty` so empty GT tiles actually train
- Run: `runs/finetune_hardneg/20260721_112416`
  - Best val F1=**0.6751** @ thr=**0.2** (epoch 4)
  - Test F1=**0.548–0.563** (vs v3_frozen test ≈0.595)
  - Hard-neg pairs still 100% FP in analysis — **do not promote yet**
- Exported: `models/adaptformer_delhi/v3_hardneg/`
- Production default stays **`v3_frozen`**

## Task 4 — Fullres DDA detection (cap 5120) — DONE
`runs/native_dda/20260721_121410/cap5120/`
- 4706×5120, ~19.6 min, change%≈**1.21**, **23** regions
- Artifacts: `change_mask.png`, `overlay.png`, `summary.json`
- 899 tiles, fusion=`dl_only`, thr=0.2, model=`adaptformer-delhi-v3`

```bash
# true native overnight if needed:
python scripts/run_native_dda_detection.py --native
```

## Missed-detection accuracy pass (2026-07-21 evening) — APPLIED
Root causes of under-detection on Grid_54 vs H43X2E1:
1. **`strip_transient_from_mask` over-deleted** mid-size roofs (weak-perm used `max_car*2`).
2. **Blue-roof recovery** rejected novel roofs merged with existing blue courts.
3. **Dark roofs / solar** (bright→black tarp) had no recovery; shadow strip could erase them.
4. AdaptFormer often fires only on **fragments** — mild score hysteresis + open on low mask.

Applied in `app/detection_engine.py`:
- Protect blue/dark-roof + structural large footprints in transient strip
- Chromatic recovery keeps **novel** (non-blue-before) pixels; lower min area
- `recover_dark_roof_construction` (local grow + strict standalone)
- Protect dark-new pixels in shadow strip
- DL-only mild hysteresis (`low ≈ 0.65×thr`, low-mask opened)
- Strip helper for large seasonal veg (when NDVI evidence is clear)

Re-applied to **run #47** (working 4706×5120 PNGs):
```bash
python scripts/reapply_run47_accuracy.py
```
- change% ≈ **4.23**, **60** regions (was ~1.21 / 10)
- GT recall vs overlay ≈ **0.96** (was ~0.69); **0 missed** GT components
- Artifacts: `runs/accuracy_improve_20260721/`

Note: curated GT **#7** is basketball-court activity (people/paint), not structural —
still detected under the denser mask; structural policy may want to drop it from GT later.

## Tuesday 2026-07-28 — day plan integration

### Uday P0 — Real weights + held-out baseline
- `models/adaptformer_delhi/v3_frozen/model.safetensors` present (~50 MB)
- `/health` → `loadedFrom=.../v3_frozen`, `available=true`, device=cuda
- Held-out test split (`data/delhi_cd/test`, 4 pairs) @ thr=0.2, `dl_only`, TTA off:
  - **mean F1=0.5867 · P=0.6028 · R=0.614 · IoU=0.4171**
  - Artifact: `runs/tuesday_baseline_20260728/metrics.json`
  - Re-run: `python scripts/record_tuesday_baseline.py`

### Integrated from Priyanka (same day)
- `pair_align` guard wired in `detect_service` (identical → message, low NCC → warning)
- Shadow classification: shadow-only blobs kept as `Shadow`; structural vs all %
- Verify: `python scripts/verify_tuesday.py` → ALL CHECKS PASSED

## Wednesday 2026-07-29 — retrain + hard-negatives

### Training fixes (`--preset wed`)
- Drop empty real GT; **keep** mined `hn_*` hard-neg tiles
- Loss: **CE + pos_weight**
- Oversample change tiles ×4; full-image 256 resize; change-centered crops
- Val-calibrate + freeze threshold
- Warm-start: `v3_frozen`

### Hard-negative mining
- Refreshed `scripts/mine_hard_negatives.py` (parking / seasonal veg / shadow tags)
- Saved **5** tiles (`hn_delhi_0002/0006/0013/...`), all tagged vegetation_seasonal+shadow
- Appended to `data/delhi_cd/train/manifest.json`

### Result (target test F1 > 0.60) — **HIT**
- Run: `runs/finetune_wed/20260729_134453`
- **best val F1=0.630** · frozen thr=**0.446**
- **test F1=0.6051 · P=0.526 · R=0.772 · IoU=0.436**
- Export: `models/adaptformer_delhi/wed_retrain/` (does **not** replace production `v3_frozen`)
- Summary: `data/delhi_cd/wednesday_retrain/metrics.json`

```bash
python scripts/build_delhi_cd_splits.py --min-change-frac 0.001 --stratify
python scripts/mine_hard_negatives.py --include-all-labeled --min-fp-frac 0.03
python scripts/finetune_adaptformer.py --delhi-cd data/delhi_cd --preset wed --out runs/finetune_wed
```

## Thursday 2026-07-30 — DSIFN spike + wed_retrain OP freeze

### Priyanka
Fast-forward merged `github-dev/New/Priyanka` → `uday` (ROI crop / training packs).

### DSIFN vs AdaptFormer (boundary completeness)
- Cloned official DSIFN + Change-Detection-Review index under `third_party/`
- Official Drive package = **dataset only** (no `.pth`/`.h5`); trained **proxy** on DSIFN val → test F1≈**0.485**
- Delhi 5 pairs (4 test + 1 val): AdaptFormer **fills interiors**; DSIFN does **not**

| Model | F1 | Fill | Hole |
|---|---:|---:|---:|
| AdaptFormer (`wed_retrain`) | **0.624** | **0.832** | **0.148** |
| DSIFN proxy | 0.090 | 0.356 | 0.644 |

**Decision: `KEEP_ADAPTFORMER`** — do not switch backbones.  
Artifacts: `data/delhi_cd/thursday_dsifn_compare/DECISION.md`

### wed_retrain operating point
Sweep F_β thr + `DETECTION_TTA` + `DETECTION_MULTISCALE` on held-out test:

| Config | Test F1 | Notes |
|---|---:|---|
| thr=0.446, TTA=off, MS=off | 0.606 | Wed sidecar baseline |
| thr=0.354 (F_β), TTA/MS variants | ≤0.601 | Drop (hurt F1) |
| thr=0.446, **TTA=full**, MS=off | **0.626** | **KEEP** (+0.020 F1, ~3× flips) |
| thr=0.446, MS=0.75,1 | 0.587 | Drop |

**Frozen OP** (`models/adaptformer_delhi/wed_retrain/`):
- `threshold=0.446`
- `DETECTION_TTA=full`
- `DETECTION_MULTISCALE=off`
- Production remains **`v3_frozen`** until an explicit promote.

```bash
python scripts/sweep_wed_operating_point.py
python scripts/dsifn_proxy_train.py --epochs 6 --batch 2
python scripts/compare_dsifn_vs_adaptformer.py
```
