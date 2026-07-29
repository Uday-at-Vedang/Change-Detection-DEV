# -*- coding: utf-8 -*-
"""Crop small geo-aligned ROI blocks from a georeferenced before/after pair.

Prototypes the Wednesday ROI backend (windowed native crop + common-grid
alignment) AND produces ready-made small test pairs under data/roi_test/.

Usage:
    venv\\Scripts\\python scripts\\make_roi_test_blocks.py <before.tif> <after.tif>

ROI blocks are fractional {x, y, w, h} in [0,1] of the images' geographic
overlap, so they're resolution-independent (the same contract the real ROI
feature will use).
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

# Named test blocks (fractions of the geographic overlap). Tune as needed.
BLOCKS = {
    "buildings": {"x": 0.34, "y": 0.66, "w": 0.16, "h": 0.16},   # the two new roofs
    "buildings_wide": {"x": 0.28, "y": 0.52, "w": 0.32, "h": 0.34},  # roofs + car row
    "parking": {"x": 0.30, "y": 0.10, "w": 0.24, "h": 0.22},     # cars only (nuisance)
}


def crop_block(before_path, after_path, roi, out_dir, name):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.warp import Resampling, reproject, transform_bounds

    with rasterio.open(before_path) as b, rasterio.open(after_path) as a:
        b_in_a = transform_bounds(b.crs, a.crs, *b.bounds)
        left = max(b_in_a[0], a.bounds.left)
        bottom = max(b_in_a[1], a.bounds.bottom)
        right = min(b_in_a[2], a.bounds.right)
        top = min(b_in_a[3], a.bounds.top)
        ow, oh = right - left, top - bottom

        # fractional ROI -> geographic sub-box (y measured from the top)
        gx0 = left + roi["x"] * ow
        gx1 = gx0 + roi["w"] * ow
        gy1 = top - roi["y"] * oh
        gy0 = gy1 - roi["h"] * oh

        res_x, res_y = a.res
        W = max(1, int(round((gx1 - gx0) / res_x)))
        H = max(1, int(round((gy1 - gy0) / res_y)))
        dst_tf = from_origin(gx0, gy1, res_x, res_y)

        def warp(src):
            out = np.zeros((3, H, W), dtype=np.uint8)
            for i in range(3):
                reproject(
                    source=rasterio.band(src, i + 1), destination=out[i],
                    src_transform=src.transform, src_crs=src.crs,
                    dst_transform=dst_tf, dst_crs=a.crs,
                    resampling=Resampling.bilinear,
                )
            return np.ascontiguousarray(out.transpose(1, 2, 0))

        bb, aa = warp(b), warp(a)

    out_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(bb).save(out_dir / f"{name}_before.png")
    Image.fromarray(aa).save(out_dir / f"{name}_after.png")
    print(f"  {name:16s} {W}x{H}px  ({W*res_x:.1f}m x {H*res_y:.1f}m)  "
          f"-> {out_dir / (name + '_{before,after}.png')}")
    return bb, aa


def main():
    if len(sys.argv) >= 3:
        before, after = Path(sys.argv[1]), Path(sys.argv[2])
    else:
        before = Path(r"C:\Users\Priyanka\Downloads\1.tif")
        after = Path(r"C:\Users\Priyanka\Downloads\2.tif")
    out_dir = Path(__file__).resolve().parent.parent / "data" / "roi_test"
    print(f"source before: {before.name}\nsource after : {after.name}\nout: {out_dir}")
    for name, roi in BLOCKS.items():
        crop_block(before, after, roi, out_dir, name)
    print("done")


if __name__ == "__main__":
    main()
