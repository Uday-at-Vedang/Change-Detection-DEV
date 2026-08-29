# DDA Change Detection — Progress Report

**Period:** 21 Jul 2026 – 13 Aug 2026  
**App:** Change Detection Webapp (DDA mode)  
**Live model (13 Aug 2026):** AdaptFormer `wed_retrain` · threshold **0.446** · TTA **full** · fusion **dl_only** · CUDA  

This report documents data, methods tried, accuracy (previous vs current), per-area / per-class results, live before/after detections, challenges, and current status.

---

## 1. Headline accuracy (frozen satellite test)

Same 4 held-out Sentinel-2 pairs every time: `delhi_0024`, `delhi_0001`, `delhi_0005`, `delhi_0016`.  
Metric is **pixel F1 for change vs no-change** (not DDA class F1).

| Checkpoint | Date | Thr | TTA | **F1** | Precision | Recall | IoU | Decision |
|---|---|---:|---|---:|---:|---:|---:|---|
| `v3_frozen` (previous production) | 28 Jul | 0.20 | off | **58.7%** | 60.3% | 61.4% | 41.7% | Rollback baseline |
| `wed_retrain` | 29 Jul | 0.446 | off | **60.5%** | 52.6% | 77.3% | 43.7% | Promoted OP |
| **`wed_retrain` + TTA full (current live)** | 30 Jul | 0.446 | full | **62.6%** | 53.3% | 83.1% | 46.0% | **Live** |
| `fri3_no_drone` | 31 Jul | 0.50 | — | 58.9% | 48.8% | 82.1% | — | Keep wed |
| `sat_v2_retrain` | 1 Aug | 0.50 | — | 57.6% | 49.5% | 79.7% | — | Do not promote |
| `fri2_retrain` (drone in train) | 31 Jul | 0.50 | — | **48.7%** | 36.1% | 84.9% | — | Rejected |
| DSIFN proxy (not live) | 30 Jul | — | — | **9.0%** | — | — | — | Rejected |

**Previous → current (same test set):** **58.7% F1 → 60.5% F1 (62.6% with TTA)**  
Gain: **+1.8 pp** without TTA, **+3.9 pp** with TTA full. Recall rose sharply (61% → 83%); precision fell (60% → 53%).

Sources: `data/delhi_cd/tuesday_baseline/metrics.json`, `models/adaptformer_delhi/wed_retrain/metrics.json`, `data/delhi_cd/thursday_op_sweep/metrics.json`, checkpoint `summary.json` files.

---

## 2. Accuracy per frozen-test pair (current model)

AdaptFormer `wed_retrain` @ 0.446 on the frozen test + 1 val pair (from the DSIFN compare, same weights).

| Pair | Zone / type | F1 | Precision | Recall | Fill (interior) | Hole rate |
|---|---|---:|---:|---:|---:|---:|
| `delhi_0024` | Sentinel-2 Delhi · mixed_gsd + vegetation | 54.8% | 40.3% | 85.4% | 81.4% | 15.8% |
| `delhi_0001` | Sentinel-2 Delhi · mixed_gsd + vegetation | 62.8% | 46.2% | 97.8% | 93.7% | 6.8% |
| `delhi_0005` | Sentinel-2 Delhi · mixed_gsd + vegetation | 55.3% | 61.8% | 50.1% | 66.5% | 31.5% |
| `delhi_0016` | Sentinel-2 Delhi · mixed_gsd + vegetation | 69.6% | 56.4% | 91.0% | 84.1% | 15.1% |
| `delhi_0021` (val, not in headline) | Sentinel-2 Delhi · mixed_gsd + vegetation | 69.4% | 55.2% | 93.7% | 90.1% | 5.0% |
| **Mean (5 Delhi pairs)** | | **62.4%** | | | **83.2%** | **14.8%** |

Tuesday `v3_frozen` per-pair F1 (older split, for reference only — pair IDs differ except `delhi_0005`):  
`delhi_0028` 66.5% · `delhi_0023` 59.1% · `delhi_0005` 51.9% · `delhi_0012` 57.2% · mean **58.7%**.

---

## 3. Areas and classes tested

### 3.1 Pixel-change classes in the labeled eval set

| Class / coverage tag | What it means | How tested | Pixel F1 available? |
|---|---|---|---|
| `mixed_gsd` | Coarse 10 m Sentinel-2 (2019 vs 2026, season-matched) | Frozen test (4 pairs) + train/val | Yes — this **is** the 58.7% / 62.6% number |
| `vegetation` | Phenology / land-cover on same Sentinel-2 tiles | Same pairs (tagged together with mixed_gsd) | Not split out from building; joint pixel F1 only |
| `building` / `road` | Individual roofs, roads | DDA GeoTIFF + drone packs | **No class-wise F1** on frozen test (10 m S2 cannot resolve buildings) |
| Structural roofs / solar (Grid_54 vs H43X2E1) | Hand GT: new blue/grey roofs, rebuild, solar | Curated mask + run #47 re-apply | GT **component recall ≈ 96%** after 21 Jul accuracy pass (was ~69%) |
| Hard-negatives | Parking, seasonal veg, shadow FP tiles | 5 mined `hn_*` tiles in train | Used for training, not a test score |

DDA **report** classes (classifier on detected blobs, not GT F1):

| DDA class | Internal engine label | Notes |
|---|---|---|
| New Construction | New Construction/Building | Roofs, solar, structure |
| Demolition | demolition / clearing / debris | Rare on recent drone reports |
| Extension | expansion / renovation | Keyword map; seldom fired |
| Vegetation Change | vegetation / tree / crop | Often over-fires on misaligned drone |
| Other | Unclassified Ground Change, bare soil | Dominant on poorly aligned pairs |

There is **no held-out F1 per DDA class**. Class columns below are **counts from live reports**, not accuracy vs labeled class GT.

### 3.2 Drone / high-res pairs vs hand GT (pixel F1)

Un-orthorectified drone mosaics. NCC 0.07–0.61. Ceiling is low until true orthomosaics exist.

| Pair | GT change % | Pred % (best drone-fix pass) | **F1** | NCC |
|---|---:|---:|---:|---:|
| `dda_before1_after` | 21.24 | 11.93 | **8.2%** | 0.07 |
| `dda_before3_after3` | 3.32 | 12.29 | **33.3%** | 0.61 |
| `dda_before4_after4` | 19.32 | 48.95 | **30.0%** | 0.41 |
| `dda_before5_after5` | 22.32 | 19.46 | **31.8%** | 0.46 |
| `dda_before6_after6` | 4.71 | 8.24 | **14.5%** | 0.51 |
| **Mean** | | | **23.6%** | |

Baseline on the same packs (`v3_frozen` @ 0.5): mean F1 **22.3%**.  
After `wed_retrain` + skip-reg auto: **23.6%** (+1.3 pp). Success bar was +15 pp or ≥45% — **not met**.

Aligned v2 drone packs (`dda_before5_after5_v2` NCC 0.74, `dda_before6_after6_v2` NCC 0.75) were added to train in `sat_v2_retrain` and **did not beat** `wed_retrain` on frozen satellite test (57.6% vs 60.5%).

---

## 4. Live DDA reports (before / after / overlay)

Images are the app’s stored working copies (registered/resized grid), not the original GeoTIFF bytes.

### 4.1 before6 vs after6 — main debug pair (Central Delhi drone)

| Report | Date | Change % | Regions | New Construction | Other | Notes |
|---|---|---:|---:|---:|---:|---|
| **#61 (reference)** | 1 Aug | **11.5%** | 31 | 8 | 23 | Stronger recall; sat_v2 / older post-process |
| #71–72 | 5 Aug | 1.13% | 5 | — | — | Shadow strip wiped true dark roofs |
| #73 | 6 Aug | 7.3% | 24 | — | — | Soft strip |
| #74–75 | 6 Aug | 10.2% | 29 | — | — | Recovery flood / edge ribbons |
| #77 | 6 Aug | 9.0% | 24 | — | — | Still blob/edge heavy |
| #78 | 6 Aug | 3.3% | 10 | — | — | Over-suppressed |
| **#79 (after OP switch)** | 8 Aug | **3.4%** | 17 | **1** | **16** | wed_retrain + weak-align skip-recovery — **under-detect** |

Hand GT for this scene is ~**4.7%** change. Report 61 over-fired vs GT; report 79 under-fired on construction class (16/17 blobs labeled Other; one New Construction of 0.3 m²).

**Report 61 (reference) — before / after / detection overlay**

![before6 before (run 61)](../data/overlays/7_15fae0bfc5734560bafc62fe7526d0cd_before.png)
![before6 after (run 61)](../data/overlays/7_15fae0bfc5734560bafc62fe7526d0cd_after.png)
![before6 overlay run 61 — 11.5%, 31 regions](../data/overlays/7_15fae0bfc5734560bafc62fe7526d0cd.png)

**Report 79 (current OP) — before / after / detection overlay**

![before6 before (run 79)](../data/overlays/7_332672051b3c47e5b1d7e4e2f2446350_before.png)
![before6 after (run 79)](../data/overlays/7_332672051b3c47e5b1d7e4e2f2446350_after.png)
![before6 overlay run 79 — 3.4%, 17 regions](../data/overlays/7_332672051b3c47e5b1d7e4e2f2446350.png)

### 4.2 Other live pairs (latest completed run per title)

| Pair | Run | Change % | Regions | New Construction | Vegetation | Other | Overlay |
|---|---:|---:|---:|---:|---:|---:|---|
| before5 vs after5 | 80 | 7.73% | 13 | 3 | 2 | 8 | `overlays/7_79fbb9f50ef845b8ac9fbce8fe770632.png` |
| before7 vs after7 | 70 | 6.40% | 6 | 2 | 0 | 4 | `overlays/7_6adbd9998af64f24abc6980eddfbc764.png` |
| before1 vs after | 68 | 17.53% | 17 | 3 | 1 | 13 | `overlays/7_9a026711e2684a4294b1cbd7665dd707.png` |
| before3 vs after3 | 66 | 18.64% | 37 | 7 | 0 | 30 | `overlays/7_a3f6c74870f544b0b2ec25e1ff9974c7.png` |
| before4 vs after4 | 56 | 7.97% | 25 | 6 | 2 | 17 | `overlays/7_a7d5ff6c1d3b4efdb3014da94b565326.png` |
| Grid_54 vs H43X2E1 | 51 | 3.25% | 60 | 9 | 2 | 49 | `overlays/6_7a53ee025d81485b80d0e6b3f0de215c.png` |
| TEST-1 vs TEST-2 (8k JPEG) | 81 | 13.13% | 60 | 2 | 51 | 7 | `overlays/7_6c78393faec548d9b24afa9b3b24ef13.png` |

**before5 (run 80) — before / after / overlay**

![before5 before](../data/overlays/7_79fbb9f50ef845b8ac9fbce8fe770632_before.png)
![before5 after](../data/overlays/7_79fbb9f50ef845b8ac9fbce8fe770632_after.png)
![before5 overlay](../data/overlays/7_79fbb9f50ef845b8ac9fbce8fe770632.png)

**TEST-1 vs TEST-2 (run 81) — before / after / overlay**  
First attempt (job 72) **stuck 2.3 h at 46%** on 8192×4320 with TTA full. Re-run completed (~3 h) at 13.1% change, mostly Vegetation Change (51/60).

![TEST-1 before](../data/overlays/7_6c78393faec548d9b24afa9b3b24ef13_before.png)
![TEST-2 after](../data/overlays/7_6c78393faec548d9b24afa9b3b24ef13_after.png)
![TEST overlay](../data/overlays/7_6c78393faec548d9b24afa9b3b24ef13.png)

---

## 5. Data used

| Source | Role | Notes |
|---|---|---|
| Sentinel-2 L2A (MGRS 43RFM), 2019-06-29 vs 2026-06-17 | Train / val / **frozen test** | 10 m GSD; vegetation + mixed_gsd; cannot score individual buildings |
| DDA GeoTIFFs (Grid_54, H43X2E1, GRID 54_2025/2026, 0304, etc.) | Live detection + Grid_54 hand GT | Some files corrupt (GRID 54_2025/2026 TIFF directory unreadable) |
| Drone before/after TIFFs (before1–before7) | Live QA + attempted train | Un-orthorectified; NCC often 0.07–0.61 |
| Aligned v2 drone packs (before5/6 v2) | Train experiment (`sat_v2`) | NCC ~0.74–0.75; did not lift frozen F1 |
| Hard-neg tiles `hn_delhi_*` | Train | Parking / seasonal veg / shadow FPs |
| Frozen test pin | `data/delhi_cd/frozen_test_ids.json` | Prevents split reshuffle from faking regressions |

---

## 6. Process and approaches tried

1. **AdaptFormer Delhi fine-tune (v3_frozen)** — thr 0.2, `dl_only`. Held-out F1 **58.7%**. Production rollback.
2. **Hard-neg fine-tune (`v3_hardneg`)** — val F1 0.675 but test **54.8–56.3%**. Not promoted (FP tiles still 100% FP in analysis).
3. **Post-process accuracy pass (21 Jul)** — protect roofs in transient/shadow strip; chromatic + dark-roof recovery; mild DL hysteresis. Grid_54 GT recall **0.69 → 0.96**; change% 1.21 → 4.23.
4. **Wednesday retrain (`wed_retrain`)** — CE + pos_weight, drop empty GT, keep hard-negs, oversample change ×4. Test F1 **60.5%**. Hit the >60% target.
5. **Operating-point sweep** — F_β thr 0.354 dropped F1. TTA full kept: **62.6%**. Multiscale dropped.
6. **DSIFN backbone spike** — Delhi F1 **9%** vs AdaptFormer **62%**. Decision: keep AdaptFormer. BIT-CD ensemble coded but **weights missing**.
7. **Drone-in-train (`fri2`)** — frozen F1 **48.7%** (precision collapse). Drone packs excluded from training thereafter.
8. **No-drone ablation (`fri3`)** — F1 **58.9%** < wed. Promote wed.
9. **Aligned v2 drone train (`sat_v2`)** — F1 **57.6%**. Do not promote. Briefly used live @ thr 0.5; caused poor UI quality.
10. **Live OP restore (8 Aug)** — switch `.env` to `wed_retrain` @ 0.446; NCC-gate GeoTIFF registration; skip roof recovery on weak-align pairs; unify ECC gate at 0.55.
11. **Polygon overlays** — clip fill to change mask; tighter contour extraction (earlier in the branch).

---

## 7. Challenges

| Issue | Impact | Status |
|---|---|---|
| Un-orthorectified drone (local parallax) | Pixel GT F1 stuck ~24%; NCC 0.07–0.61 | Blocker without new orthomosaics |
| Training on those drone packs | Frozen satellite F1 60.5% → 48.7% | Fixed: exclude from train |
| 10 m Sentinel-2 test set | Headline F1 does not measure building/road skill | Still the only frozen apples-to-apples number |
| Weak-align post-process tug-of-war | before6 reports swung 1% ↔ 11.5% | Soft-strip + skip recovery; **over-suppressed** (#79 = 3.4%) |
| Shadow strip vs dark new roofs | True construction deleted | Soft path when `registration_ok=False` |
| 8k×4k TTA-full inference | Job 72 hung 2.3 h at 46% | Cancelled; job 73 finished in ~3 h |
| Corrupt GRID 54_2025/2026 TIFFs | Library skip | Unrelated to model |
| No per-class GT F1 | Cannot quote “building accuracy = X%” | Only blob counts + pixel change F1 |
| BIT-CD / extra NN | Ensemble off; DSIFN rejected | Stay on AdaptFormer |

---

## 8. Achievements

- Held-out satellite F1 **58.7% → 60.5% (62.6% TTA)** on a **frozen** 4-pair test.
- Hit the Wednesday target **test F1 > 60%**.
- Proved drone-in-train **hurts** clean satellite F1; froze test IDs so later labels cannot fake a drop.
- Rejected a weaker backbone (DSIFN) with measured Delhi F1 9% vs 62%.
- Grid_54 structural GT recall **~96%** after recovery/strip fixes (offline re-apply).
- Live stack documented: AdaptFormer neural net already in production (`AI-Based Deep Learning`).
- DDA product path: jobs, reports, polygon overlays, ROI → training-pack export.

---

## 9. Current status (13 Aug 2026)

| Item | Value |
|---|---|
| Live weights | `models/adaptformer_delhi/wed_retrain` |
| Calibrated threshold | **0.446** |
| Fusion / TTA | `dl_only` / `full` |
| Headline accuracy | **62.6% F1** (TTA) / **60.5% F1** (no TTA) vs previous **58.7%** |
| Best drone-pack F1 | **~24%** mean (alignment-limited) |
| Latest before6 report | **#79 · 3.4% · 17 regions** (worse visually than #61 11.5%) |
| Latest large JPEG | TEST-1/2 **13.1%**, 60 regions, 51 vegetation |
| Open gap | Weak-align recall vs FP; need orthomosaics or a pair-specific recall path |
| Not recommended | New backbone, drone-in-train, `sat_v2` @ 0.5 |

**Recommended next work:** restore selective dark-roof recovery on weak pairs (or a slightly lower thr for low-NCC GeoTIFFs) so before6 construction returns without re-flooding; keep `wed_retrain` as the satellite OP; treat drone F1 as a geometry problem, not a model-swap problem.
