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

## Friday 2026-07-31 — integrate + regression (Priyanka, Task 1)

### Labeling round (drone pairs, before splits)
Hand/assisted-labeled 9 drone before/after pairs (georeferenced but
**un-orthorectified**, NCC 0.07–0.61 — well below the ~0.9 needed for clean
pixel correspondence). Registration attempts (SIFT homography, ECC
affine/euclidean/homography on the static region) could not raise NCC further
— confirmed **local parallax**, not a fixable global misalignment; orthomosaic
reprocessing from raw photos is the only real fix. See each pack's `meta.json`
(`orthomosaics_needed: true`) for the caveat.

By explicit user directive (2026-07-31), these 9 are now flagged
`training_use: true` (`training_use_override` recorded in each `meta.json`)
and registered in `docs/delhi_eval/manifest.json` with pack-local
`before.png`/`after.png` (not the original Downloads TIFFs, so the split is
portable to Uday's machine):

| pair_id | change_frac | split |
|---|---:|---|
| `dda_1_2_scene` | 0.0079 | train |
| `dda_after_testing` | 0.1604 | train |
| `dda_before1_after` | 0.2124 | train |
| `dda_before1_after2_testing` | 0.2342 | test |
| `dda_before2_after2` | 0.3396 | val |
| `dda_before3_after3` | 0.0332 | val |
| `dda_before4_after4` | 0.1932 | train |
| `dda_before5_after5` | 0.2232 | train |
| `dda_before6_after6` | 0.0471 | train |

An earlier aligned-source labeling attempt (2019→2026 satellite pair, 1
positive + 3 seasonal hard-negatives, `dda_before2019_r0..r3`) was created,
then **removed by user request** before ingest — not present in the splits.

### Splits rebuilt
```bash
python scripts/build_delhi_cd_splits.py --min-change-frac 0.001 --stratify
```
33 labeled pairs → **train=23 · val=5 · test=5** (was 24/16·4·4 before today).

### Regression check
- `python scripts/validate_detection.py` (unit checks) — **all pass**, no change
  from HEAD.
- `python scripts/validate_detection.py --benchmark --method "AI-Based Deep
  Learning"` — synthetic-scene F1 is very low (0.00–0.06) vs the old
  `runs/calibration/best_params.json` numbers (0.86–1.0). **Not a regression
  from this week's commits** — confirmed via `git diff` that
  `feature_based_method`/fusion logic are byte-identical to before Tuesday.
  Root cause: those old numbers were measured under `smart_union` fusion
  (classical+DL) on a different model; current default is `dl_only`
  (`app/detection_config.py:200` docstring: `smart_union` "collapsed mean F1
  from ~0.58 to ~0.04" on the v3 Delhi model — a deliberate, documented
  tradeoff). The synthetic suite's expected baseline is stale for `dl_only`
  and should be recalibrated separately; it is **not** something Tue–Fri
  changes broke.
- End-to-end wiring smoke (ROI crop → detect → export training pack →
  ingest) — **all 4 steps pass**, artifacts cleaned up.

### Shadow classification — removed (user decision)
Reverted Tuesday's Shadow DDA type / region classifier
(`change_type_map.py`, `detection_engine.py`, `detect_service.py`).
Shadow-only pixels are still **suppressed** (`strip_shadow_only_from_mask`,
unchanged) — just no longer surfaced as a labeled class or split stat.

### ROI → training-pack export — shipped
`POST /api/dda/training/pack` (`app/dda/training_pack.py`,
`app/dda/training_routes.py`) + `scripts/ingest_dda_gt_label.py --pair-id`
generalization + "Export training pack" button in the result view
(`static/js/dda/result.js`). Verified end-to-end (see regression check above).

### 🚩 Found + fixed: test-set drift risk (before handing off to Uday)
Rebuilding the splits (needed to fold in Friday's labels) reshuffled which
pairs land in test **even at the same seed** — Wednesday/Thursday's frozen
`wed_retrain` operating point (thr=0.446, TTA=full, test F1=0.6263) and
Tuesday's `v3_frozen` baseline (F1=0.5867) were both measured on
`{delhi_0024, delhi_0001, delhi_0005, delhi_0016}`. The first Friday rebuild
produced a **different** test set (`{delhi_0024, delhi_0011, delhi_0017,
delhi_0022, dda_before1_after2_testing}`, only 1 pair overlapping) — any F1
number from that set would **not** be comparable to Tue/Wed/Thu's, and a drop
could look like "the model got worse" when it's really "the benchmark
changed."

**Fix implemented:** `scripts/build_delhi_cd_splits.py` now supports
`--freeze-test-ids` (default: `data/delhi_cd/frozen_test_ids.json`), which
pins specific pair_ids to test on every run regardless of what's
added/removed from the labeled pool. Created
`data/delhi_cd/frozen_test_ids.json` = the original Tue/Wed/Thu set. Rebuilt
splits — **test is now exactly `{delhi_0024, delhi_0001, delhi_0005,
delhi_0016}` again**, so today's held-out eval is directly comparable to the
whole week's numbers. All 9 drone pairs land in **train (8) / val (1)** —
none in test — so the benchmark stays 100% clean satellite imagery
regardless of the drone-data alignment-quality question (see below).

Residual minor risk: `dda_before2019` removal + drone-pair addition also
shifted **val** composition (`dda_1_2_scene`, a low-NCC pair, is now in val).
Val doesn't affect the headline test-F1 comparison but does feed the
val-threshold sweep (Thursday's `sweep_wed_operating_point.py` pattern) — if
Uday re-runs that sweep, note val is not fully frozen, only test.

### Handoff to Uday (Friday P0)
- Fine-tune on the rebuilt 24/5/4 split (train includes 8 of the 9 new drone
  pairs — low-NCC caveat applies; test is unchanged/frozen, clean satellite
  only).
- Held-out eval on the **frozen test set** → before/after F1/P/R vs Tuesday's
  `v3_frozen` baseline (now a valid, apples-to-apples comparison) →
  promotion decision (`v3_frozen` vs `wed_retrain` vs new fine-tune).
- **Recommended extra check:** since low-NCC drone pairs are now in training,
  additionally report F1 on the frozen test set split by training-data
  recipe (with vs without the 9 drone pairs, if time allows an ablation) to
  isolate whether they helped or hurt real (clean satellite) performance —
  directly answers "did the drone data degrade F1."
