"""Write a paint-ready GT-labeling pack for a before/after pair (optionally an ROI).

Produces ``docs/delhi_eval/dda_labeling/<pair_id>/`` with:
  - before.png / after.png   (RGB previews)
  - seed_mask.png            (draft change mask to edit; blank if none)
  - gt_mask_blank.png        (empty mask to paint from scratch)
  - LABELING.md / meta.json
and registers the pair in ``docs/delhi_eval/manifest.json``.

Shared by the offline ``scripts/prepare_dda_gt_labeling.py`` and the
``POST /api/dda/training/pack`` endpoint so both write identical packs that
``scripts/ingest_dda_gt_label.py --pair-id <id>`` can pull back in.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from PIL import Image

_ROOT = Path(__file__).resolve().parent.parent.parent
LABELING_DIR = _ROOT / "docs" / "delhi_eval" / "dda_labeling"
MANIFEST = _ROOT / "docs" / "delhi_eval" / "manifest.json"


def make_pair_id(before_name: str, after_name: str, roi: Optional[dict] = None) -> str:
    """Stable, filesystem-safe pair id from the two filenames (+ ROI hash)."""
    base = f"{Path(before_name).stem}_{Path(after_name).stem}".lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    if roi:
        tag = hashlib.md5(json.dumps(roi, sort_keys=True).encode()).hexdigest()[:6]
        return f"dda_{base}_roi_{tag}"
    return f"dda_{base}"


def rasterize_regions(regions, src_w: int, src_h: int, dst_w: int, dst_h: int) -> np.ndarray:
    """Coarse seed mask: fill each region's bbox, scaled from the detection grid
    (``src_w`` x ``src_h``) to the pack preview grid (``dst_w`` x ``dst_h``)."""
    mask = np.zeros((dst_h, dst_w), np.uint8)
    sx = dst_w / max(1, src_w)
    sy = dst_h / max(1, src_h)
    for r in regions or []:
        bb = r.get("bbox") or {}
        x = int(round(float(bb.get("x", 0)) * sx))
        y = int(round(float(bb.get("y", 0)) * sy))
        w = int(round(float(bb.get("w", 0)) * sx))
        h = int(round(float(bb.get("h", 0)) * sy))
        if w > 0 and h > 0:
            mask[max(0, y):min(dst_h, y + h), max(0, x):min(dst_w, x + w)] = 255
    return mask


def _guide_text(pair_id: str) -> str:
    return f"""# Labeling pack: {pair_id}

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
python scripts/ingest_dda_gt_label.py --pair-id {pair_id}
python scripts/build_delhi_cd_splits.py
```
"""


def _register_manifest(pair_id, before_path, after_path, w, h, *,
                       roi=None, zone="", gsd=None, change_types=None) -> int:
    """Add/replace the pair in the eval manifest. Returns total pair count."""
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    if MANIFEST.is_file():
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    else:
        data = {"pairs": []}

    def _rel(p):
        try:
            return str(Path(p).resolve().relative_to(_ROOT)).replace("\\", "/")
        except Exception:
            return str(p).replace("\\", "/")

    pairs = [p for p in (data.get("pairs") or []) if p.get("pair_id") != pair_id]
    pairs.append({
        "pair_id": pair_id,
        "before_path": _rel(before_path),
        "after_path": _rel(after_path),
        "date_before": None,
        "date_after": None,
        "gsd": gsd,
        "zone": zone or "",
        "change_types": change_types or ["building", "road", "vegetation", "mixed"],
        "gt_mask": None,
        "roi": roi,
        "notes": (
            f"Labeling pack. Paint docs/delhi_eval/dda_labeling/{pair_id}/gt_mask.png "
            f"then run scripts/ingest_dda_gt_label.py --pair-id {pair_id}"
        ),
        "label_preview_size": [w, h],
    })
    data["pairs"] = pairs
    MANIFEST.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return len(pairs)


def write_labeling_pack(before_rgb, after_rgb, seed_mask, *, pair_id,
                        before_path, after_path, roi=None, zone="",
                        gsd=None, change_types=None) -> dict:
    """Write a full labeling pack + manifest entry. Returns a summary dict."""
    out = LABELING_DIR / pair_id
    out.mkdir(parents=True, exist_ok=True)
    h, w = before_rgb.shape[:2]

    Image.fromarray(before_rgb).save(out / "before.png")
    Image.fromarray(after_rgb).save(out / "after.png")
    blank = np.zeros((h, w), np.uint8)
    Image.fromarray(blank).save(out / "gt_mask_blank.png")

    if seed_mask is None:
        seed = blank
    else:
        seed = np.asarray(seed_mask)
        if seed.ndim == 3:
            seed = seed[:, :, 0]
        if seed.shape[:2] != (h, w):
            seed = cv2.resize(seed, (w, h), interpolation=cv2.INTER_NEAREST)
    seed = (seed > 127).astype(np.uint8) * 255
    Image.fromarray(seed).save(out / "seed_mask.png")
    seed_px = int((seed > 127).sum())

    meta = {
        "pair_id": pair_id,
        "before": str(before_path),
        "after": str(after_path),
        "preview_shape": [h, w],
        "roi": roi,
        "seed_changed_px": seed_px,
        "created_unix": time.time(),
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    (out / "LABELING.md").write_text(_guide_text(pair_id), encoding="utf-8")
    n_pairs = _register_manifest(
        pair_id, before_path, after_path, w, h,
        roi=roi, zone=zone, gsd=gsd, change_types=change_types)

    return {
        "pairId": pair_id,
        "dir": str(out.relative_to(_ROOT)).replace("\\", "/"),
        "previewSize": [w, h],
        "seedChangedPx": seed_px,
        "manifestPairs": n_pairs,
    }
