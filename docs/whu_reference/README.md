# WHU Reference Set — Real-Data Pipeline Test (NOT Delhi)

Purpose: validate the detection harness against real satellite imagery and
real building footprints, while the actual Delhi evaluation set
(`docs/delhi_eval/`) is still being curated. **This is not a Delhi substitute
and is not used for any Delhi F1 target** — see `Accuracy_Improvement_Plan.xlsx`
("Non-negotiable first step: Build a Delhi evaluation set... LEVIR-CD tiles
do not represent Delhi imagery"). This dataset has the same domain-mismatch
problem relative to Delhi that LEVIR-CD does.

## Source

WHU Building Dataset, "Satellite dataset I (global cities)" —
<https://gpcv.whu.edu.cn/data/building_dataset.html>. 204 image tiles
(512x512, multiple satellite sensors, 0.3–2.5m GSD) with hand-delineated
building-footprint labels. Downloaded to `data/whu_reference/raw/` (gitignored,
not committed — see `.gitignore`).

We originally attempted the WHU **Building Change Detection Dataset**
(genuine 2012→2016 Christchurch, NZ bi-temporal pairs, 5.43GB) but the source
server was too unreliable from this network (stalled repeatedly, one stall
lasted ~2.7h; ~50% downloaded in ~4h before we gave up). Switched to the
113MB single-time dataset instead.

## How the pairs were built

`scripts/build_whu_reference_pairs.py` turns single-time tiles into
semi-synthetic before/after pairs:
- `after` = the original real tile (buildings present)
- `before` = the same tile with the building-mask region inpainted away
  (`cv2.inpaint`, Telea)
- `gt` = the real building-footprint label (== the synthetic "change" region)

This is **not a genuine bi-temporal pair** — no real second acquisition, no
real illumination/season/registration differences. It's useful for pipeline
plumbing and rough sensitivity checks, not for accuracy claims.

Regenerate with:
```bash
python scripts/build_whu_reference_pairs.py --count 5
python scripts/compare_methods.py --manifest data/whu_reference/pairs/manifest.json \
    --methods "AI-Based Deep Learning,Feature-Based,Hybrid Approach" --sensitivities 0.5 \
    --out runs/whu_reference_test
```

## Result (2026-07-13, sensitivity=0.5, 5 tiles x 3 methods)

Mean IoU=0.042, F1=0.078 — high precision (0.46-1.0), very low recall
(0.03-0.14). The pipeline ran correctly end-to-end (no crashes, masks
aligned, metrics computed), but under-detects real building-shaped change at
default sensitivity. Directionally consistent with the plan's Day 4
calibration step being needed — not a substitute for it.

Full per-pair numbers: `runs/whu_reference_test/manifest_report.json` (gitignored).
