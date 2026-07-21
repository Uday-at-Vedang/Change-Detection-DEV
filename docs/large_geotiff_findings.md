# Large GeoTIFF Detection — Verification Findings

Verifies the RCA (Root Cause Analysis) claims against the actual codebase and a
real large GeoTIFF, since production input = large single-GeoTIFF uploads (not
the 300x300px Delhi tiles used in the Day 1-7 calibration work).

## Test setup

- Real Sentinel-2 imagery, 8192x8192px pair (2019-06-29 vs 2026-06-17, Delhi
  MGRS tile 43RFM), pulled via windowed COG read into
  `data/large_geotiff_test/` (~180 MB each, gitignored).
- Run through the actual `run_detection()` pipeline, AI-Based Deep Learning
  method, sensitivity 0.5.

## RCA issue verification (code + empirical)

| # | RCA Issue | Verified status |
|---|---|---|
| 1 | Default mode `downscaled` destroys detail | **CONFIRMED.** Default is `downscaled`, `DETECTION_MAX_SIDE=4096`. A 50x30px building on a 50,000px-wide GeoTIFF -> 4x2px after downscale. `fullres_tiled` mode exists but is off by default. |
| 2 | AdaptFormer/LEVIR-CD domain mismatch | **CONFIRMED** (Day 3, `model_changed_px≈0`). Fix = fine-tuning (Uday's track). |
| 3 | Vegetation suppression kills real change | **NOT AN ISSUE.** `git blame` shows the correct asymmetric suppress/boost logic was already merged 2026-04-11. RCA describes an older code snapshot. |
| 4 | Registration 0.45x penalty on SSIM/edge | **Code real, impact negligible.** A/B tested on 4 real pairs: <0.001% difference in final score (downstream normalization cancels the uniform scaling). |
| 5-7 | Windowed threshold / tile seams / min_region_area scaling | Downstream of Issue 1; require the fullres path to be exercised. Partially addressed by the timing finding below. |

## Empirical performance finding (the key production blocker)

CPU-only inference at production scale is **impractically slow**:

| Mode | Resolution | change% | Regions | Completed on CPU? |
|---|---|---|---|---|
| `downscaled` (default) | 4096px | 0.614% | 11 | Yes — ~61 min |
| `fullres_tiled` (capped) | 5120px | 0.464% | **18** | Yes — but needed overnight (machine idled/throttled; ~46 min active CPU observed early, then stalled) |
| `fullres_tiled` (true native) | 8192px | — | — | **No** — >5h across two attempts, never completed |

**Two findings:**

1. **The fix works.** `fullres_tiled` produces more granular detections (18
   regions vs 11) — higher resolution breaks up what downscaling merges into
   fewer coarse blobs, so smaller/finer changes are caught. Issue 1's fix path
   is functional end-to-end, not just theoretical. (Lower total change% with
   *more* regions = finer, more localized detections rather than a few
   over-merged coarse ones.)

2. **But it's impractical on CPU.** Even the *current default* `downscaled`
   mode takes ~1 hour per pair on an 8192px input; true native `fullres_tiled`
   never completed in >5h. This is the same root constraint crashing Uday's
   fine-tuning (CPU can't handle larger datasets) — **GPU is required for both
   the detection and training tracks at production scale, not optional.**

## Recommendation

1. **Issue 1 fix** (highest leverage): auto-detect `fullres_tiled` for GeoTIFF
   inputs, OR change the default — BUT this is only usable with GPU inference,
   given the timing above.
2. **GPU provisioning** is the gating dependency for the whole large-GeoTIFF
   goal — surfaced independently by both the detection timing and the training
   crashes.
3. The Day 1-7 Delhi calibration (`cl_q=0.90`, +53% F1 on small tiles) remains
   valid for small/pre-tiled inputs but is **unverified at production GeoTIFF
   scale** — do not present it as production-proven without that caveat.

_Generated during Day 7+ large-GeoTIFF verification. Raw run logs under `runs/`._
