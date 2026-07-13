# Delhi Evaluation Set

Real before/after image pairs (+ hand-labeled ground-truth masks) used to
measure change-detection accuracy against actual Delhi imagery, instead of
LEVIR-CD tiles or synthetic cases. This is the primary metric for the
accuracy-improvement sprint (see `Accuracy_Improvement_Plan.xlsx`); LEVIR and
the synthetic benchmark (`scripts/validate_detection.py --benchmark`) remain
regression gates only.

## Layout

```
docs/delhi_eval/
  manifest.json       # pair registry — see schema below
  labels/              # binary GT change masks, one PNG per pair (added Day 2-3)
    <pair_id>.png
```

## manifest.json schema

```json
{
  "pairs": [
    {
      "pair_id": "delhi_0001",
      "before_path": "library_sources/2024/site_a.tif",
      "after_path": "library_sources/2026/site_a.tif",
      "date_before": "2024-03-10",
      "date_after": "2026-02-18",
      "gsd": 0.3,
      "zone": "South Delhi",
      "change_types": ["building"],
      "gt_mask": null,
      "notes": ""
    }
  ]
}
```

| Field | Required | Notes |
|---|---|---|
| `pair_id` | yes | unique, auto-assigned as `delhi_%04d` |
| `before_path` / `after_path` | yes | path relative to repo root, must exist on disk |
| `date_before` / `date_after` | no | `YYYY-MM-DD` if known |
| `gsd` | no | ground sample distance in meters, if known |
| `zone` | no | free-text area/locality label |
| `change_types` | yes | subset of `building`, `road`, `vegetation`, `mixed_gsd`, `other` — drives coverage checks |
| `gt_mask` | yes (auto) | set once `docs/delhi_eval/labels/<pair_id>.png` exists; `null` until labeled |
| `notes` | no | anything unusual about the pair (misalignment, cloud cover, etc.) |

## Current status (2026-07-13)

32 pairs logged via `scripts/build_delhi_pairs_sentinel2.py`, sourced from free
Sentinel-2 L2A imagery (Copernicus/AWS Open Data, MGRS tile 43RFM), covering
Delhi's western/southwestern periphery (the tile doesn't reach the far-east
Trans-Yamuna area). `2019-06-29` vs `2026-06-17` — deliberately season-matched
(same time of year, ~7 years apart) so diffs reflect real structural change
rather than monsoon/crop-calendar swings.

**This satisfies the pair-count target but not full category coverage:**
- `mixed_gsd` and `vegetation`: covered (32 pairs each)
- `building` and `road`: **not covered** — Sentinel-2 is 10m GSD, too coarse
  to resolve individual buildings/roads reliably. Real building/road-level
  pairs still need either DDA's own GeoTIFFs (see
  `docs/IMPLEMENTATION_PLAN_DDA.md`, "Blocked until DDA provides...") or
  another higher-resolution source.
- GT masks: **not generated** — hand-labeling in QGIS/LabelMe is still a
  separate step (see Workflow below). Diff-based scores were only used to
  *select* likely-changed locations, not as ground truth.

Regenerate/extend with:
```bash
python scripts/build_delhi_pairs_sentinel2.py --count 30
```

## Workflow

```bash
# one-time
python scripts/build_delhi_manifest.py --init

# see what imagery is on disk to help pick pairs
python scripts/build_delhi_manifest.py --scan

# log a pair once you've picked it
python scripts/build_delhi_manifest.py --add \
    --before library_sources/2024/site_a.tif --after library_sources/2026/site_a.tif \
    --zone "South Delhi" --gsd 0.3 --change-types building,road

# check progress against the >=30 pairs / coverage target
python scripts/build_delhi_manifest.py --validate

# sanity-run the detection harness across every pair in the manifest
# (no GT needed yet — this is the Day 1 "runs end-to-end" check)
python scripts/compare_methods.py --manifest docs/delhi_eval/manifest.json --out runs/manifest_scan
```

Ground-truth masks (`labels/<pair_id>.png`) are added in the next pass
(hand-labeled in QGIS/LabelMe); `--validate` will flag pairs still missing one.
