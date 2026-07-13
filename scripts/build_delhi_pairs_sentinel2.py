"""
Curate real Delhi before/after pairs from free, openly-licensed Sentinel-2
imagery (Copernicus / AWS Open Data, via the Earth Search STAC API), and
register them into docs/delhi_eval/manifest.json.

Why: DDA (the client) hasn't handed off sample GeoTIFFs yet (see
docs/IMPLEMENTATION_PLAN_DDA.md, "Blocked until DDA provides..."), and no
imagery exists in library_sources/. This gets a *real*, freely-licensed Delhi
dataset in place now so calibration/harness work isn't blocked on that
handoff. Caveat: Sentinel-2 is 10m GSD — good for large land-use change
(new colonies, vegetation loss, water bodies), NOT reliable for individual
building/road-level detail. Treat this as a coarse-GSD complement to finer
imagery (DDA's own GeoTIFFs, or drone/high-res satellite), not a replacement.

GT masks are NOT generated here (diff-based "ground truth" would be circular
and would bake in seasonal/illumination noise as if it were real change).
Masks still need hand-labeling per docs/delhi_eval/README.md.

Usage:
    python scripts/build_delhi_pairs_sentinel2.py --count 30
"""
import argparse
import json
import os
from pathlib import Path

import numpy as np
import rasterio
import rasterio.warp
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "delhi_eval" / "manifest.json"
LIBRARY_ROOT = ROOT / "library_sources"

# Same MGRS tile (43RFM), near-zero cloud cover, same season (mid/late June)
# ~7 years apart — season-matched so diffs reflect real structural change
# rather than crop-calendar swings (Delhi's agri belt looks wildly different
# in Nov vs Jul purely from monsoon/rabi-kharif cycles). Picked via STAC.
BEFORE_URL = ("https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
              "43/R/FM/2019/6/S2A_43RFM_20190629_1_L2A/TCI.tif")
AFTER_URL = ("https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
             "43/R/FM/2026/6/S2B_43RFM_20260617_0_L2A/TCI.tif")
DATE_BEFORE = "2019-06-29"
DATE_AFTER = "2026-06-17"

# Delhi NCT bbox (west, south, east, north), clipped to this tile's footprint
# (tile covers lon <= ~77.15, so far-east Delhi across the Yamuna is excluded).
DELHI_BBOX_LONLAT = (76.84, 28.40, 77.15, 28.80)
CELL_PX = 300  # ~3km x 3km at 10m GSD
NODATA_FRACTION_LIMIT = 0.15  # skip cells that are >15% black fill


def _lonlat_to_pixel_bbox(ds, bbox_lonlat):
    from rasterio.warp import transform_bounds
    west, south, east, north = transform_bounds("EPSG:4326", ds.crs, *bbox_lonlat)
    row_start, col_start = ds.index(west, north)
    row_stop, col_stop = ds.index(east, south)
    return max(0, row_start), min(ds.height, row_stop), max(0, col_start), min(ds.width, col_stop)


def _read_cell(ds, row, col, size):
    window = Window(col, row, size, size)
    arr = ds.read([1, 2, 3], window=window)
    return np.transpose(arr, (1, 2, 0))  # HWC


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--cell-px", type=int, default=CELL_PX)
    args = parser.parse_args()

    print(f"Opening COGs (windowed HTTP reads, no full download)...\n  before={BEFORE_URL}\n  after={AFTER_URL}")
    with rasterio.open(BEFORE_URL) as before_ds, rasterio.open(AFTER_URL) as after_ds:
        assert before_ds.transform == after_ds.transform and before_ds.crs == after_ds.crs, \
            "before/after grids are not pixel-aligned"

        row0, row1, col0, col1 = _lonlat_to_pixel_bbox(before_ds, DELHI_BBOX_LONLAT)
        size = args.cell_px
        candidates = []
        rows = range(row0, row1 - size, size)
        cols = range(col0, col1 - size, size)
        print(f"Scanning {len(list(rows))}x{len(list(cols))} grid of {size}x{size}px cells for change signal...")

        for row in range(row0, row1 - size, size):
            for col in range(col0, col1 - size, size):
                before = _read_cell(before_ds, row, col, size)
                after = _read_cell(after_ds, row, col, size)
                nodata_frac = max(
                    np.mean(np.all(before == 0, axis=-1)),
                    np.mean(np.all(after == 0, axis=-1)),
                )
                if nodata_frac > NODATA_FRACTION_LIMIT:
                    continue
                diff_score = float(np.mean(np.abs(before.astype(np.int16) - after.astype(np.int16))))
                green_before = before[..., 1].astype(np.int16)
                green_after = after[..., 1].astype(np.int16)
                veg_signal = float(np.mean(np.abs(green_before - green_after)))
                easting, northing = rasterio.transform.xy(before_ds.transform, row + size // 2, col + size // 2)
                lon, lat = rasterio.warp.transform(before_ds.crs, "EPSG:4326", [easting], [northing])
                lon, lat = lon[0], lat[0]
                candidates.append({
                    "row": row, "col": col, "diff_score": diff_score,
                    "veg_signal": veg_signal, "lon": lon, "lat": lat,
                    "before": before, "after": after,
                })

        print(f"{len(candidates)} valid (non-nodata) cells scanned.")
        candidates.sort(key=lambda c: -c["diff_score"])
        selected = candidates[: args.count]

        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8")) if MANIFEST_PATH.exists() else {"pairs": []}
        existing_ids = {p["pair_id"] for p in manifest["pairs"]}
        next_num = len(manifest["pairs"]) + 1

        before_dir = LIBRARY_ROOT / DATE_BEFORE[:4]
        after_dir = LIBRARY_ROOT / DATE_AFTER[:4]
        before_dir.mkdir(parents=True, exist_ok=True)
        after_dir.mkdir(parents=True, exist_ok=True)

        base_crs = before_ds.crs

        added = 0
        for cand in selected:
            while f"delhi_{next_num:04d}" in existing_ids:
                next_num += 1
            pair_id = f"delhi_{next_num:04d}"
            fname = f"sentinel2_delhi_r{cand['row']}_c{cand['col']}.tif"

            transform = window_transform(Window(cand["col"], cand["row"], size, size), before_ds.transform)
            profile = dict(
                driver="GTiff", width=size, height=size, count=3, dtype="uint8",
                crs=base_crs, transform=transform, compress="deflate",
            )

            before_path = before_dir / fname
            after_path = after_dir / fname
            with rasterio.open(before_path, "w", **profile) as dst:
                dst.write(np.transpose(cand["before"], (2, 0, 1)))
            with rasterio.open(after_path, "w", **profile) as dst:
                dst.write(np.transpose(cand["after"], (2, 0, 1)))

            change_types = ["mixed_gsd"]
            if cand["veg_signal"] > cand["diff_score"] * 0.6:
                change_types.append("vegetation")

            manifest["pairs"].append({
                "pair_id": pair_id,
                "before_path": str(before_path.relative_to(ROOT)),
                "after_path": str(after_path.relative_to(ROOT)),
                "date_before": DATE_BEFORE,
                "date_after": DATE_AFTER,
                "gsd": 10.0,
                "zone": f"Delhi ({cand['lat']:.4f}N, {cand['lon']:.4f}E)",
                "change_types": change_types,
                "gt_mask": None,
                "notes": "Sentinel-2 L2A TCI, 10m GSD — coarse resolution, NOT reliable for individual "
                         "building/road detail. Real Delhi coordinates/dates. GT mask still needs hand-labeling.",
            })
            existing_ids.add(pair_id)
            added += 1
            print(f"  {pair_id}  ({cand['lat']:.4f}N, {cand['lon']:.4f}E)  diff_score={cand['diff_score']:.1f}"
                  f"  tags={change_types}")

        MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        print(f"\nAdded {added} pair(s). Manifest now has {len(manifest['pairs'])} total.")
        print(f"Images written to {before_dir.relative_to(ROOT)}/ and {after_dir.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
