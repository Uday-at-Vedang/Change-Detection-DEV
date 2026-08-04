"""Verify strip_shadow_fragments_from_mask (CPU-only, no server/GPU needed).

Confirms the new residual-shadow-fragment filter removes the jagged, low-fill
leftovers a misaligned pair's shadow boundary leaves behind, WITHOUT touching
solid real-change regions or regressing the existing synthetic regression
suite (scripts/validate_detection.py's four tracked cases).

Run:  venv\\Scripts\\python scripts\\verify_shadow_fragment_strip.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np

from app.detection_engine import strip_shadow_fragments_from_mask

_fails = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails.append(name)


print("=" * 64)
print("SELECTIVE REMOVAL - solid change kept, jagged fringe stripped")
print("=" * 64)

H, W = 300, 300
before = np.full((H, W, 3), 140, np.uint8)
after = before.copy()

# Solid new-construction block: real material/colour change, high fill ratio.
after[40:100, 40:120] = [190, 150, 110]
mask = np.zeros((H, W), np.uint8)
mask[40:100, 40:120] = 255

# Branching residual fringe: low fill, low chroma shift, just darker (the
# exact leftover a strict per-pixel shadow strip leaves on a jittery edge).
star = np.zeros((H, W), np.uint8)
cv2.line(star, (180, 150), (220, 190), 255, 3)
cv2.line(star, (200, 150), (170, 200), 255, 3)
cv2.line(star, (190, 160), (230, 170), 255, 3)
cv2.line(star, (185, 145), (215, 210), 255, 4)
star = cv2.dilate(star, np.ones((3, 3), np.uint8))
after[star > 0] = (before[star > 0].astype(np.float32) * 0.55).astype(np.uint8)
mask[star > 0] = 255

out = strip_shadow_fragments_from_mask(mask, before, after)
solid_kept = float((out[40:100, 40:120] > 0).mean())
frag_kept = float((out[star > 0] > 0).mean()) if star.sum() else 0.0
check("solid new-construction region preserved", solid_kept > 0.90, f"{solid_kept:.0%} kept")
check("jagged shadow fringe removed", frag_kept < 0.10, f"{frag_kept:.0%} kept")

print("=" * 64)
print("BROAD SOLID SHADOW - a blocky (non-jagged) blob is still stripped")
print("=" * 64)

# A building's shadow falling across open ground is a compact, high-solidity
# blob -- not thin/branching -- but it is still just the same ground surface
# darkened, not a new material. Geometry alone must not save it.
before2 = np.full((H, W, 3), 150, np.uint8)
after2 = before2.copy()
after2[60:160, 60:160] = (before2[60:160, 60:160].astype(np.float32) * 0.55).astype(np.uint8)
mask2 = np.zeros((H, W), np.uint8)
mask2[60:160, 60:160] = 255

out4 = strip_shadow_fragments_from_mask(mask2, before2, after2)
shadow_kept = float((out4[60:160, 60:160] > 0).mean())
check("broad solid cast-shadow blob removed", shadow_kept < 0.10, f"{shadow_kept:.0%} kept")

# A genuinely new, differently-coloured solid block must survive untouched.
before3 = np.full((H, W, 3), 150, np.uint8)
after3 = before3.copy()
after3[60:160, 60:160] = [200, 140, 90]
mask3 = np.zeros((H, W), np.uint8)
mask3[60:160, 60:160] = 255
out5 = strip_shadow_fragments_from_mask(mask3, before3, after3)
building_kept = float((out5[60:160, 60:160] > 0).mean())
check("solid differently-coloured new block preserved", building_kept > 0.90, f"{building_kept:.0%} kept")

print("=" * 64)
print("SAFETY - a mask with only real change is left untouched")
print("=" * 64)

mask_solid_only = np.zeros((H, W), np.uint8)
mask_solid_only[40:100, 40:120] = 255
out2 = strip_shadow_fragments_from_mask(mask_solid_only, before, after)
check("no false removal when nothing is fragment-like",
      np.array_equal(out2, mask_solid_only))

print("=" * 64)
print("EDGE CASES")
print("=" * 64)

check("None mask passes through unchanged",
      strip_shadow_fragments_from_mask(None, before, after) is None)
check("empty mask stays empty",
      int((strip_shadow_fragments_from_mask(np.zeros((H, W), np.uint8), before, after) > 0).sum()) == 0)
mismatched = np.zeros((H, H), np.uint8)
check("shape mismatch returns input untouched (no crash)",
      np.array_equal(strip_shadow_fragments_from_mask(mismatched, before, after), mismatched))

print("=" * 64)
print("OFFICIAL SYNTHETIC REGRESSION SUITE - zero regression required")
print("=" * 64)

from scripts.validate_detection import (  # noqa: E402
    _case_brightness_only, _case_inserted_buildings,
    _case_misaligned_change, _case_parked_cars,
)

for case_name, case_fn in [
    ("inserted_buildings", _case_inserted_buildings),
    ("parked_cars", _case_parked_cars),
    ("misaligned_change", _case_misaligned_change),
    ("brightness_only", _case_brightness_only),
]:
    b, a, gt, _ = case_fn()
    out3 = strip_shadow_fragments_from_mask(gt.copy(), b, a)
    kept = int((out3 > 0).sum())
    total = int((gt > 0).sum())
    check(f"{case_name}: real/expected pixels unaffected", kept == total,
          f"{kept}/{total} px kept")

print("=" * 64)
if _fails:
    print(f"RESULT: {len(_fails)} CHECK(S) FAILED -> {', '.join(_fails)}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
