"""
One-off: turn WHU 'Satellite Dataset I' single-time building tiles into
semi-synthetic before/after change pairs, so the detection harness can be
smoke-tested against real satellite imagery + real building footprints
instead of purely synthetic geometry.

NOT a Delhi substitute — this is a real-data *pipeline* test set only (see
docs/whu_reference/README.md). Each pair is built from ONE real acquisition:
    after  = the original tile (buildings present)
    before = the same tile with building-mask pixels inpainted away
    gt     = the real building-footprint label (== the "change" region)

Usage:
    python scripts/build_whu_reference_pairs.py --src "data/whu_reference/extracted/Satellite dataset Ⅰ (global cities)" \
        --out data/whu_reference/pairs --count 5
"""
import argparse
import glob
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent


def _building_pct(label: np.ndarray) -> float:
    return float(np.mean(label > 127)) * 100


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default="", help="path to the extracted 'Satellite dataset I' folder; "
                        "auto-detected under data/whu_reference/extracted/ if omitted (zip entry names use "
                        "a Roman numeral that some unzip tools mangle)")
    parser.add_argument("--out", default="data/whu_reference/pairs")
    parser.add_argument("--count", type=int, default=5)
    parser.add_argument("--min-pct", type=float, default=3.0, help="min building coverage %% to consider a tile")
    parser.add_argument("--max-pct", type=float, default=25.0, help="max building coverage %% to consider a tile")
    args = parser.parse_args()

    if args.src:
        src = Path(args.src)
    else:
        extracted_root = ROOT / "data" / "whu_reference" / "extracted"
        subdirs = [p for p in extracted_root.iterdir() if p.is_dir()] if extracted_root.exists() else []
        if len(subdirs) != 1:
            raise SystemExit(f"Expected exactly one folder under {extracted_root}, found {len(subdirs)}. "
                              f"Pass --src explicitly.")
        src = subdirs[0]
        print(f"Auto-detected source: {src}")
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    image_paths = sorted(glob.glob(str(src / "image" / "*.tif")))
    candidates = []
    for img_path in image_paths:
        tile_id = Path(img_path).stem
        label_path = src / "label" / f"{tile_id}.tif"
        if not label_path.exists():
            continue
        label = np.array(Image.open(label_path).convert("L"))
        pct = _building_pct(label)
        if args.min_pct <= pct <= args.max_pct:
            candidates.append((tile_id, img_path, str(label_path), pct))

    if not candidates:
        raise SystemExit(f"No tiles with building coverage in [{args.min_pct}, {args.max_pct}]%")

    candidates.sort(key=lambda c: c[3])  # spread across coverage levels
    step = max(1, len(candidates) // args.count)
    selected = candidates[::step][: args.count]

    manifest = {"pairs": []}
    for tile_id, img_path, label_path, pct in selected:
        image = np.array(Image.open(img_path).convert("RGB"))
        label = np.array(Image.open(label_path).convert("L"))
        building_mask = (label > 127).astype(np.uint8) * 255

        # Dilate slightly so inpainting also erases building shadows/edges.
        kernel = np.ones((7, 7), np.uint8)
        inpaint_mask = cv2.dilate(building_mask, kernel, iterations=1)
        before = cv2.inpaint(image, inpaint_mask, inpaintRadius=7, flags=cv2.INPAINT_TELEA)

        pair_dir = out_dir / tile_id
        pair_dir.mkdir(parents=True, exist_ok=True)
        Image.fromarray(before).save(pair_dir / "before.png")
        Image.fromarray(image).save(pair_dir / "after.png")
        Image.fromarray(building_mask).save(pair_dir / "gt.png")

        manifest["pairs"].append({
            "pair_id": f"whu_{tile_id}",
            "before_path": str((pair_dir / "before.png").relative_to(ROOT)),
            "after_path": str((pair_dir / "after.png").relative_to(ROOT)),
            "gt_mask": str((pair_dir / "gt.png").relative_to(ROOT)),
            "building_pct": round(pct, 2),
            "source": "WHU Satellite Dataset I (global cities) — semi-synthetic pair, real imagery + real building footprint, inpainted 'before'",
        })
        print(f"  {tile_id}: building_pct={pct:.1f}%  -> {pair_dir.relative_to(ROOT)}")

    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nWrote {len(manifest['pairs'])} pair(s) to {manifest_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
