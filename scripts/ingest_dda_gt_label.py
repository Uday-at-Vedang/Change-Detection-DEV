"""
Ingest a hand-edited GT mask for dda_grid54_h43x2e1 into docs/delhi_eval.

Looks for (in order):
  docs/delhi_eval/dda_labeling/dda_grid54_h43x2e1/gt_mask.png
  docs/delhi_eval/dda_labeling/dda_grid54_h43x2e1/seed_mask.png  (if --allow-seed)

Copies to docs/delhi_eval/labels/dda_grid54_h43x2e1.png and updates manifest.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PAIR_ID = "dda_grid54_h43x2e1"
MANIFEST = ROOT / "docs/delhi_eval/manifest.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair-id", type=str, default=DEFAULT_PAIR_ID,
                    help="Labeling-pack pair id (folder under docs/delhi_eval/dda_labeling/)")
    ap.add_argument("--allow-seed", action="store_true",
                    help="Accept seed_mask.png if gt_mask.png is missing (draft only)")
    ap.add_argument("--src", type=str, default="",
                    help="Optional explicit path to a binary mask PNG")
    args = ap.parse_args()

    pair_id = args.pair_id
    pack = ROOT / "docs/delhi_eval/dda_labeling" / pair_id
    dest = ROOT / "docs/delhi_eval/labels" / f"{pair_id}.png"

    src = Path(args.src) if args.src else pack / "gt_mask.png"
    if not src.is_file() and args.allow_seed:
        src = pack / "seed_mask.png"
    if not src.is_file():
        print(f"Missing {src}. Finish labeling first (see {pack / 'LABELING.md'}).")
        return 1

    arr = np.array(Image.open(src).convert("L"))
    binary = ((arr > 127).astype(np.uint8) * 255)
    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(binary).save(dest)
    changed = float((binary > 127).mean())
    print(f"Wrote {dest} shape={binary.shape} change_frac={changed:.4f}")

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    found = False
    for p in data.get("pairs", []):
        if p.get("pair_id") == pair_id:
            p["gt_mask"] = str(dest.relative_to(ROOT)).replace("\\", "/")
            found = True
            break
    if not found:
        print(f"WARNING: {pair_id} not in manifest — export the pack first")
    else:
        MANIFEST.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("Manifest updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
