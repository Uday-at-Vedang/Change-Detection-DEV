"""Verify Tuesday's alignment/identical guard (CPU-only, no GPU needed).

Run:  venv\\Scripts\\python scripts\\verify_tuesday.py
      [optional] pass a before/after GeoTIFF pair to also test real images:
      venv\\Scripts\\python scripts\\verify_tuesday.py <before.tif> <after.tif>

Prints PASS/FAIL per check and exits non-zero if anything fails.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

_fails = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails.append(name)


print("=" * 62)
print("TASK 1 - Alignment / identical-input guard")
print("=" * 62)

from app.dda.pair_align import are_identical, assess_alignment  # noqa: E402
from app.dda.detect_service import _alignment_warning  # noqa: E402
from PIL import Image  # noqa: E402

# 1a. identical arrays are flagged
img = np.random.default_rng(0).integers(0, 255, (128, 128, 3), dtype=np.uint8)
check("identical arrays detected", are_identical(img, img.copy()))

# 1b. a shifted (misaligned) copy scores lower NCC than a perfect match
base = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8)
_, ncc_same = assess_alignment(base, base.copy())
_, ncc_shift = assess_alignment(base[:180, :180], base[10:190, 10:190])
check("aligned pair scores high NCC", ncc_same > 0.9, f"NCC={ncc_same:.2f}")
check("misaligned pair scores lower NCC", ncc_shift < ncc_same, f"NCC={ncc_shift:.2f}")

# 1c. genuinely-different non-geotiff pair produces NO false warning
a = Image.fromarray(np.random.default_rng(2).integers(0, 255, (128, 128, 3), dtype=np.uint8))
b = Image.fromarray(np.random.default_rng(3).integers(0, 255, (128, 128, 3), dtype=np.uint8))
check("no false warning on different non-geotiff pair",
      _alignment_warning(a, b, None, None) is None)

# 1d. identical PILs produce the 'same image' warning
w = _alignment_warning(a, a.copy(), None, None)
check("identical pair -> 'same image' warning", bool(w) and "same image" in w.lower())

# 1e. optional: real GeoTIFF pair from the command line
if len(sys.argv) >= 3:
    bp, ap = Path(sys.argv[1]), Path(sys.argv[2])
    if bp.is_file() and ap.is_file():
        wr = _alignment_warning(Image.open(bp), Image.open(ap), str(bp), str(ap))
        print(f"  [INFO] real pair warning: {wr!r}")


print("=" * 62)
print("SHADOW REMOVAL - shadows are suppressed, NOT classified/detected")
print("=" * 62)

import app.dda.change_type_map as ctm  # noqa: E402
from app.dda.change_type_map import map_to_dda_change_type  # noqa: E402
import app.detection_engine as de  # noqa: E402
from app.detection_engine import strip_shadow_only_from_mask  # noqa: E402

# No Shadow DDA type / classifier remains
check("no DDA_SHADOW type defined", not hasattr(ctm, "DDA_SHADOW"))
check("no _shadow_regions_from_mask classifier", not hasattr(de, "_shadow_regions_from_mask"))
check("'shadow' no longer maps to a Shadow class",
      map_to_dda_change_type("Shadow") != "Shadow")

# Shadow-only pixels are still SUPPRESSED (stripped), so they aren't detected as change
before = np.full((300, 300, 3), 150, np.uint8)
after = before.copy()
after[100:180, 100:180] = 120                      # L drops, chroma unchanged = shadow
mask = np.zeros((300, 300), np.uint8)
mask[100:180, 100:180] = 255                       # detector flagged it as change
post = strip_shadow_only_from_mask(mask, before, after)
check("shadow-only pixels suppressed (not detected)", int((post > 127).sum()) == 0)

print("=" * 62)
if _fails:
    print(f"RESULT: {len(_fails)} CHECK(S) FAILED -> {', '.join(_fails)}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
