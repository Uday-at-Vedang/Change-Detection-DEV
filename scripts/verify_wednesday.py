"""Verify Wednesday's ROI crop-and-test backend (Task 1). CPU-only, no server.

Run:  venv\\Scripts\\python scripts\\verify_wednesday.py
      [optional] also test a native GeoTIFF window read:
      venv\\Scripts\\python scripts\\verify_wednesday.py "C:\\Users\\Priyanka\\Downloads\\1.tif"
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
from PIL import Image

from app.dda.geotiff_io import parse_roi, load_rgb_roi

_fails = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails.append(name)


print("=" * 62)
print("TASK 1 - ROI crop-and-test backend")
print("=" * 62)

# --- ROI validation ---
check("valid roi accepted", parse_roi({"x": 0.34, "y": 0.66, "w": 0.16, "h": 0.16}) is not None)
check("empty roi -> None (full image)", parse_roi(None) is None)
for bad, why in [
    ({"x": 0.9, "y": 0, "w": 0.3, "h": 0.1}, "out of bounds"),
    ({"x": 0, "y": 0, "w": 0, "h": 0.1}, "zero width"),
    ({"x": 0.1}, "missing keys"),
    ({"x": -0.1, "y": 0, "w": 0.2, "h": 0.2}, "negative origin"),
]:
    rejected = False
    try:
        parse_roi(bad)
    except ValueError:
        rejected = True
    check(f"rejects {why}", rejected)

# --- crop correctness on a normal image (PIL path) ---
# 400x300 white image with a RED square at the fractional box x[0.5-0.7], y[0.5-0.7].
arr = np.full((300, 400, 3), 255, np.uint8)
arr[150:210, 200:280] = [220, 20, 20]  # red block
tmp = Path(__file__).resolve().parent.parent / "data" / "roi_test" / "_verify_tmp.png"
tmp.parent.mkdir(parents=True, exist_ok=True)
Image.fromarray(arr).save(tmp)

crop = np.asarray(load_rgb_roi(tmp, {"x": 0.5, "y": 0.5, "w": 0.2, "h": 0.2}))
h, w = crop.shape[:2]
check("crop has expected size", abs(w - 80) <= 2 and abs(h - 60) <= 2, f"{w}x{h} (want ~80x60)")
r, g, b = crop[..., 0].mean(), crop[..., 1].mean(), crop[..., 2].mean()
check("crop landed on the target (red) region", r > 150 and g < 90 and b < 90,
      f"mean RGB=({r:.0f},{g:.0f},{b:.0f})")

full = load_rgb_roi(tmp, None)
check("roi=None -> full image (no regression)", full.size == (400, 300), f"{full.size}")
tmp.unlink(missing_ok=True)

# --- optional: native windowed read from a real large GeoTIFF ---
tif = None
if len(sys.argv) >= 2 and Path(sys.argv[1]).is_file():
    tif = Path(sys.argv[1])
else:
    cand = Path(r"C:\Users\Priyanka\Downloads\1.tif")
    if cand.is_file():
        tif = cand
if tif is not None:
    try:
        import rasterio
        with rasterio.open(tif) as ds:
            fw, fh = ds.width, ds.height
        t = time.time()
        c = load_rgb_roi(tif, {"x": 0.34, "y": 0.66, "w": 0.16, "h": 0.16})
        dt = time.time() - t
        check("native ROI window read is small + fast",
              max(c.size) < max(fw, fh) and dt < 20,
              f"{c.size} from {fw}x{fh} in {dt:.2f}s")
    except ImportError:
        print("  [SKIP] rasterio not available for native GeoTIFF test")
else:
    print("  [SKIP] no GeoTIFF provided (pass one as an argument to test native window read)")

print("=" * 62)
if _fails:
    print(f"RESULT: {len(_fails)} CHECK(S) FAILED -> {', '.join(_fails)}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
