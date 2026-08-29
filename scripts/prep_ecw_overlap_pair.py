"""Warp two neighboring ECW orthos onto the same overlap grid for DDA testing.

The app cannot read .ecw (rasterio has no ECW driver). Neighboring tiles
(e.g. 0-23 vs 0-24) also fail naive NCC because they cover different ground.

This script uses QGIS GDAL (ECW plugin) to:
  1. read bounds
  2. crop both to the geographic intersection (optional center window)
  3. write matching GeoTIFFs (same CRS, pixel size, size)

Usage:
    python scripts/prep_ecw_overlap_pair.py
    python scripts/prep_ecw_overlap_pair.py --before PATH --after PATH --out DIR
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEFORE = Path(r"c:\Users\udayb\Downloads\0-24_ori _26-02-25.ecw")
DEFAULT_AFTER = Path(r"c:\Users\udayb\Downloads\0-23_ori_01-03-25.ecw")
DEFAULT_OUT = ROOT / "data" / "library_sources" / "central_delhi" / "Images"
QGIS_ROOT = Path(r"C:\Program Files\QGIS 4.0.2")


def _qgis_env() -> dict:
    env = os.environ.copy()
    bin_dir = str(QGIS_ROOT / "bin")
    env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
    env["GDAL_DRIVER_PATH"] = str(QGIS_ROOT / "apps" / "gdal" / "lib" / "gdalplugins")
    env["PROJ_LIB"] = str(QGIS_ROOT / "share" / "proj")
    gdal_data = QGIS_ROOT / "apps" / "gdal" / "share" / "gdal"
    if not gdal_data.is_dir():
        gdal_data = QGIS_ROOT / "share" / "gdal"
    if gdal_data.is_dir():
        env["GDAL_DATA"] = str(gdal_data)
    return env


def _gdalinfo(path: Path) -> dict:
    exe = QGIS_ROOT / "bin" / "gdalinfo.exe"
    raw = subprocess.check_output(
        [str(exe), "-json", str(path)], env=_qgis_env(), text=True
    )
    return json.loads(raw)


def _extent(info: dict) -> tuple[float, float, float, float]:
    c = info["cornerCoordinates"]
    xs = [c["upperLeft"][0], c["lowerLeft"][0], c["upperRight"][0], c["lowerRight"][0]]
    ys = [c["upperLeft"][1], c["lowerLeft"][1], c["upperRight"][1], c["lowerRight"][1]]
    return min(xs), min(ys), max(xs), max(ys)


def _intersect(a, b):
    xmin = max(a[0], b[0])
    ymin = max(a[1], b[1])
    xmax = min(a[2], b[2])
    ymax = min(a[3], b[3])
    if xmax <= xmin or ymax <= ymin:
        raise SystemExit("No geographic overlap — these are not the same scene.")
    return xmin, ymin, xmax, ymax


def _center_window(ext, height_m: float | None):
    xmin, ymin, xmax, ymax = ext
    if not height_m or height_m <= 0:
        return ext
    cy = 0.5 * (ymin + ymax)
    half = height_m / 2.0
    ymin2 = max(ymin, cy - half)
    ymax2 = min(ymax, cy + half)
    return xmin, ymin2, xmax, ymax2


def _warp(src: Path, dst: Path, te, tr: float) -> None:
    exe = QGIS_ROOT / "bin" / "gdalwarp.exe"
    xmin, ymin, xmax, ymax = te
    cmd = [
        str(exe), "-overwrite",
        "-t_srs", "EPSG:32643",
        "-te", str(xmin), str(ymin), str(xmax), str(ymax),
        "-tr", str(tr), str(tr),
        "-r", "bilinear",
        "-of", "GTiff",
        "-co", "TILED=YES",
        "-co", "COMPRESS=LZW",
        "-dstalpha",
        str(src), str(dst),
    ]
    subprocess.check_call(cmd, env=_qgis_env())


def crop_valid_overlap(before_path: Path, after_path: Path) -> None:
    """Drop nodata/alpha so the app's global NCC is measured on real overlap."""
    import numpy as np
    import rasterio
    from rasterio.windows import Window

    with rasterio.open(before_path) as db, rasterio.open(after_path) as da:
        b = db.read()
        a = da.read()
        if b.shape[0] >= 4:
            valid_b = b[3] > 0
            rgb_b = b[:3]
        else:
            valid_b = np.any(b[:3] > 5, axis=0)
            rgb_b = b[:3]
        if a.shape[0] >= 4:
            valid_a = a[3] > 0
            rgb_a = a[:3]
        else:
            valid_a = np.any(a[:3] > 5, axis=0)
            rgb_a = a[:3]
        both = valid_b & valid_a & np.any(rgb_b > 5, axis=0) & np.any(rgb_a > 5, axis=0)
        rows = np.any(both, axis=1)
        cols = np.any(both, axis=0)
        if not rows.any() or not cols.any():
            raise SystemExit("No jointly valid pixels after warp")
        r0, r1 = int(np.argmax(rows)), int(len(rows) - np.argmax(rows[::-1]))
        c0, c1 = int(np.argmax(cols)), int(len(cols) - np.argmax(cols[::-1]))
        window = Window(c0, r0, c1 - c0, r1 - r0)
        transform = db.window_transform(window)
        profile = db.profile.copy()
        profile.update(
            count=3,
            height=r1 - r0,
            width=c1 - c0,
            transform=transform,
            photometric="RGB",
        )
        profile.pop("nbits", None)
        out_b = rgb_b[:, r0:r1, c0:c1]
        out_a = rgb_a[:, r0:r1, c0:c1]

    with rasterio.open(before_path, "w", **profile) as dst:
        dst.write(out_b)
    with rasterio.open(after_path, "w", **profile) as dst:
        dst.write(out_a)
    print(f"cropped valid overlap to {c1 - c0} x {r1 - r0} px")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--before", type=Path, default=DEFAULT_BEFORE)
    p.add_argument("--after", type=Path, default=DEFAULT_AFTER)
    p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p.add_argument("--gsd", type=float, default=0.03, help="Output metres/pixel")
    p.add_argument(
        "--center-height-m",
        type=float,
        default=120.0,
        help="Keep this many metres of N-S overlap (0 = full strip)",
    )
    args = p.parse_args()
    if not args.before.is_file() or not args.after.is_file():
        raise SystemExit("ECW files not found")
    if not (QGIS_ROOT / "bin" / "gdalwarp.exe").is_file():
        raise SystemExit(f"QGIS GDAL not found at {QGIS_ROOT}")

    info_b = _gdalinfo(args.before)
    info_a = _gdalinfo(args.after)
    ext = _center_window(_intersect(_extent(info_b), _extent(info_a)), args.center_height_m)
    w = ext[2] - ext[0]
    h = ext[3] - ext[1]
    args.out.mkdir(parents=True, exist_ok=True)
    before_out = args.out / "ecw_overlap_before_2025-02-26.tif"
    after_out = args.out / "ecw_overlap_after_2025-03-01.tif"
    print(f"overlap window {w:.2f} x {h:.2f} m @ {args.gsd} m/px -> {before_out.name}")
    _warp(args.before, before_out, ext, args.gsd)
    _warp(args.after, after_out, ext, args.gsd)
    crop_valid_overlap(before_out, after_out)

    sys.path.insert(0, str(ROOT))
    import cv2
    from app.detection_engine import _alignment_ncc

    def load(path: Path):
        im = cv2.imread(str(path), cv2.IMREAD_COLOR)
        return cv2.cvtColor(im, cv2.COLOR_BGR2RGB)

    rb, ra = load(before_out), load(after_out)
    ncc = float(_alignment_ncc(rb, ra))

    meta = {
        "before_src": str(args.before),
        "after_src": str(args.after),
        "extent_utm43n": list(ext),
        "gsd_m": args.gsd,
        "size_px": [int(rb.shape[1]), int(rb.shape[0])],
        "ncc_full": round(ncc, 4),
        "ncc_valid_pixels": round(ncc, 4),
        "registration_gate": 0.55,
        "fit_for_detection": bool(ncc >= 0.55),
        "note": (
            "Neighboring tiles cropped to shared ground. Dates are 3 days apart "
            "so expect little real construction change; pair is for alignment/pipeline tests."
        ),
    }
    (args.out / "ecw_overlap_pair.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )
    print(json.dumps(meta, indent=2))
    if not meta["fit_for_detection"]:
        raise SystemExit("NCC still below 0.55 — pair not fit")


if __name__ == "__main__":
    main()
