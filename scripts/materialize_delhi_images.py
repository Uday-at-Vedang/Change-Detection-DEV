"""
Materialize Delhi eval GeoTIFFs referenced in manifest.json (not stored in git).

Priyanka's manifest lists library_sources/.../sentinel2_delhi_r{row}_c{col}.tif
paths. Run this once after merging New/Priyanka to fetch those windows from the
public Sentinel-2 COGs.

    python scripts/materialize_delhi_images.py
    python scripts/materialize_delhi_images.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window
from rasterio.windows import transform as window_transform

os.environ.setdefault("AWS_NO_SIGN_REQUEST", "YES")
os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "docs" / "delhi_eval" / "manifest.json"
BEFORE_URL = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "43/R/FM/2019/6/S2A_43RFM_20190629_1_L2A/TCI.tif"
)
AFTER_URL = (
    "https://sentinel-cogs.s3.us-west-2.amazonaws.com/sentinel-s2-l2a-cogs/"
    "43/R/FM/2026/6/S2B_43RFM_20260617_0_L2A/TCI.tif"
)
CELL_RE = re.compile(r"sentinel2_delhi_r(\d+)_c(\d+)\.tif$", re.I)


def _parse_cell(path: Path) -> tuple[int, int, int] | None:
    m = CELL_RE.search(path.name)
    if not m:
        return None
    row, col = int(m.group(1)), int(m.group(2))
    return row, col, 300  # Priyanka's default cell-px


def _write_cell(ds, row: int, col: int, size: int, dest: Path, dry_run: bool) -> bool:
    if dest.is_file() and dest.stat().st_size > 0:
        return False
    if dry_run:
        print(f"  would write {dest.relative_to(ROOT)}")
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    window = Window(col, row, size, size)
    arr = ds.read([1, 2, 3], window=window)
    profile = dict(
        driver="GTiff", width=size, height=size, count=3, dtype="uint8",
        crs=ds.crs, transform=window_transform(window, ds.transform), compress="deflate",
    )
    with rasterio.open(dest, "w", **profile) as dst:
        dst.write(arr)
    print(f"  wrote {dest.relative_to(ROOT)}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", type=str, default=str(MANIFEST))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
    jobs: list[tuple[Path, str, int, int, int]] = []
    for pair in manifest.get("pairs", []):
        for key in ("before_path", "after_path"):
            rel = pair.get(key)
            if not rel:
                continue
            dest = ROOT / rel
            parsed = _parse_cell(dest)
            if parsed is None:
                continue
            row, col, size = parsed
            url = BEFORE_URL if "2019" in rel else AFTER_URL
            jobs.append((dest, url, row, col, size))

    if not jobs:
        print("No Sentinel-2 cells to materialize.")
        return

    print(f"Materializing {len(jobs)} GeoTIFF window(s)...")
    written = 0
    with rasterio.open(BEFORE_URL) as before_ds, rasterio.open(AFTER_URL) as after_ds:
        for dest, url, row, col, size in jobs:
            ds = before_ds if url == BEFORE_URL else after_ds
            if _write_cell(ds, row, col, size, dest, args.dry_run):
                written += 1
    print(f"Done ({written} file(s) {'planned' if args.dry_run else 'written'}).")


if __name__ == "__main__":
    main()
