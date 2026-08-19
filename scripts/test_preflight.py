"""Smoke tests for app.dda.preflight (run: venv/Scripts/python scripts/test_preflight.py).

Builds tiny synthetic GeoTIFFs/images directly in the OS temp dir and an
in-memory SQLite session, so this needs no live server and no real library
data. Covers the 10 cases from the Image Validation preflight plan.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
import app.models  # noqa: F401 - registers User/DetectionRun on Base
import app.dda.models  # noqa: F401 - registers DetectionJob/RegionReview on Base
import app.dda.tree.models  # noqa: F401 - registers ImageLibrary/TreeNode on Base
from app.dda.tree.models import ImageLibrary
from app.dda.preflight import run_preflight_checks, read_band_count

TMP = Path(tempfile.mkdtemp(prefix="dda_preflight_test_"))

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine)


def _write_geotiff(name, west, south, east, north, width=64, height=64,
                    crs="EPSG:4326", count=3, dtype="uint8"):
    import rasterio
    from rasterio.transform import from_bounds

    path = TMP / name
    transform = from_bounds(west, south, east, north, width, height)
    rng = np.random.default_rng(0)
    data = rng.integers(0, 255, (count, height, width), dtype="uint8")
    with rasterio.open(
        path, "w", driver="GTiff", width=width, height=height, count=count,
        dtype=dtype, crs=crs, transform=transform,
    ) as dst:
        dst.write(data)
    return path


def _write_plain_png(name, width=64, height=64):
    from PIL import Image
    path = TMP / name
    rng = np.random.default_rng(1)
    arr = rng.integers(0, 255, (height, width, 3), dtype="uint8")
    Image.fromarray(arr).save(path)
    return path


def _write_garbage(name):
    path = TMP / name
    path.write_bytes(b"not a real geotiff" * 20)
    return path


def _seed_manual_bounds(db, rel_path, west, south, east, north):
    row = ImageLibrary(
        node_id=1,
        image_name=Path(rel_path).name,
        file_path=rel_path,
        bounds_json=json.dumps({"west": west, "south": south, "east": east, "north": north}),
        has_georef=True,
    )
    db.add(row)
    db.commit()


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"{status} {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")


def test_1_mismatched_crs_real_overlap(db):
    before = _write_geotiff("c1_before.tif", 77.20, 28.70, 77.21, 28.71, crs="EPSG:4326")
    import pyproj
    tf = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:32643", always_xy=True)
    ux0, uy0 = tf.transform(77.201, 28.701)
    ux1, uy1 = tf.transform(77.209, 28.709)
    after = _write_geotiff("c1_after.tif", min(ux0, ux1), min(uy0, uy1), max(ux0, ux1), max(uy0, uy1), crs="EPSG:32643")
    r = run_preflight_checks(db, before, after, "c1_before.tif", "c1_after.tif")
    check("1 mismatched-CRS overlap: no hard fail", not r.hard_fail, r.fail_reason)
    crs_checks = [c for c in r.checks if c.name == "crs"]
    check("1 mismatched-CRS overlap: no CRS warning", all(c.status != "fail" for c in crs_checks))
    check("1 mismatched-CRS overlap: overlap check ran", any(c.name == "overlap" for c in r.checks))


def test_2_non_overlapping_pair(db):
    before = _write_geotiff("c2_before.tif", 77.20, 28.70, 77.21, 28.71)
    after = _write_geotiff("c2_after.tif", 72.80, 19.00, 72.81, 19.01)  # Mumbai, far away
    r = run_preflight_checks(db, before, after, "c2_before.tif", "c2_after.tif")
    check("2 non-overlapping pair: hard fail", r.hard_fail)
    check("2 non-overlapping pair: overlap check failed", any(c.name == "overlap" and c.status == "fail" for c in r.checks))


def test_3_grayscale_pair(db):
    before = _write_geotiff("c3_before.tif", 77.20, 28.70, 77.21, 28.71, count=1)
    after = _write_geotiff("c3_after.tif", 77.20, 28.70, 77.21, 28.71, count=3)
    r = run_preflight_checks(db, before, after, "c3_before.tif", "c3_after.tif")
    check("3 grayscale pair: no hard fail", not r.hard_fail, r.fail_reason)
    check("3 grayscale pair: has a grayscale warning", any("grayscale" in w.lower() for w in r.warnings), r.warnings)


def test_4_two_band_raster(db):
    before = _write_geotiff("c4_before.tif", 77.20, 28.70, 77.21, 28.71, count=2)
    after = _write_geotiff("c4_after.tif", 77.20, 28.70, 77.21, 28.71, count=3)
    r = run_preflight_checks(db, before, after, "c4_before.tif", "c4_after.tif")
    check("4 2-band raster: hard fail", r.hard_fail)
    check("4 2-band raster: bands check failed", any(c.name == "bands" and c.status == "fail" for c in r.checks))


def test_5_no_georef_no_manual(db):
    before = _write_plain_png("c5_before.png")
    after = _write_plain_png("c5_after.png")
    r = run_preflight_checks(db, before, after, "c5_before.png", "c5_after.png")
    check("5 no georef, no manual bounds: no hard fail", not r.hard_fail, r.fail_reason)
    check("5 no georef, no manual bounds: zero warnings", len(r.warnings) == 0, r.warnings)


def test_6_manual_bounds_overlap(db):
    before = _write_plain_png("c6_before.png")
    _seed_manual_bounds(db, "c6_before.png", 77.20, 28.70, 77.21, 28.71)
    after = _write_geotiff("c6_after.tif", 77.202, 28.702, 77.208, 28.708)
    r = run_preflight_checks(db, before, after, "c6_before.png", "c6_after.tif")
    check("6 manual bounds overlap: no hard fail", not r.hard_fail, r.fail_reason)
    check("6 manual bounds overlap: has manual-bounds warning", any("manual" in w.lower() for w in r.warnings), r.warnings)
    overlap_checks = [c for c in r.checks if c.name == "overlap"]
    check("6 manual bounds overlap: overlap computed (not silently skipped)", any(c.status == "pass" for c in overlap_checks))


def test_7_gsd_mismatch(db):
    before = _write_geotiff("c7_before.tif", 77.20, 28.70, 77.21, 28.71, width=16, height=16)   # coarse
    after = _write_geotiff("c7_after.tif", 77.20, 28.70, 77.21, 28.71, width=800, height=800)     # fine
    r = run_preflight_checks(db, before, after, "c7_before.tif", "c7_after.tif")
    check("7 GSD mismatch: no hard fail", not r.hard_fail, r.fail_reason)
    check("7 GSD mismatch: has a GSD warning", any(c.name == "gsd" and c.status == "warn" for c in r.checks))


def test_8_corrupt_file(db):
    before = _write_garbage("c8_before.tif")
    after = _write_geotiff("c8_after.tif", 77.20, 28.70, 77.21, 28.71)
    r = run_preflight_checks(db, before, after, "c8_before.tif", "c8_after.tif")
    check("8 corrupt file: hard fail", r.hard_fail)
    check("8 corrupt file: readability check failed", any(c.name == "readability" and c.status == "fail" for c in r.checks))


def test_9_weak_overlap(db):
    before = _write_geotiff("c9_before.tif", 77.200, 28.700, 77.210, 28.710)
    # Same-size after footprint, shifted 90% east — ~10% overlap of the after area.
    after = _write_geotiff("c9_after.tif", 77.209, 28.700, 77.219, 28.710)
    r = run_preflight_checks(db, before, after, "c9_before.tif", "c9_after.tif")
    check("9 weak overlap: no hard fail", not r.hard_fail, r.fail_reason)
    check("9 weak overlap: overlap warning", any(c.name == "overlap" and c.status == "warn" for c in r.checks))


def test_10_zero_overlap_georeferenced(db):
    before = _write_geotiff("c10_before.tif", 77.20, 28.70, 77.21, 28.71)
    after = _write_geotiff("c10_after.tif", 77.50, 28.70, 77.51, 28.71)
    r = run_preflight_checks(db, before, after, "c10_before.tif", "c10_after.tif")
    check("10 zero overlap (georeferenced): hard fail", r.hard_fail)


def test_band_count_helper():
    rgb = _write_geotiff("bc_rgb.tif", 77.20, 28.70, 77.21, 28.71, count=3)
    gray = _write_geotiff("bc_gray.tif", 77.20, 28.70, 77.21, 28.71, count=1)
    two = _write_geotiff("bc_two.tif", 77.20, 28.70, 77.21, 28.71, count=2)
    check("band count: RGB=3", read_band_count(rgb) == 3)
    check("band count: gray=1", read_band_count(gray) == 1)
    check("band count: 2-band=2", read_band_count(two) == 2)


if __name__ == "__main__":
    db = SessionLocal()
    try:
        test_band_count_helper()
        test_1_mismatched_crs_real_overlap(db)
        test_2_non_overlapping_pair(db)
        test_3_grayscale_pair(db)
        test_4_two_band_raster(db)
        test_5_no_georef_no_manual(db)
        test_6_manual_bounds_overlap(db)
        test_7_gsd_mismatch(db)
        test_8_corrupt_file(db)
        test_9_weak_overlap(db)
        test_10_zero_overlap_georeferenced(db)
        print("ALL PASS")
    finally:
        db.close()
