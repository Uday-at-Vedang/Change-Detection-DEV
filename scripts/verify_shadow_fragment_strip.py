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
print("MERGED BLOB - shadow arm touching a real building is still stripped")
print("=" * 64)

# A real building whose own cast shadow reaches just past its footprint and
# touches a jagged fragment merges them into ONE connected component. The
# building's strong colour signal must not protect the attached shadow arm.
before4 = np.full((H, W, 3), 150, np.uint8)
after4 = before4.copy()
after4[40:110, 40:110] = [200, 140, 90]  # real, differently-coloured building
mask4 = np.zeros((H, W), np.uint8)
mask4[40:110, 40:110] = 255

arm = np.zeros((H, W), np.uint8)
cv2.line(arm, (108, 108), (108, 220), 255, 3)
cv2.line(arm, (108, 200), (170, 230), 255, 4)
cv2.line(arm, (108, 180), (150, 170), 255, 3)
arm = cv2.dilate(arm, np.ones((3, 3), np.uint8))
after4[arm > 0] = (before4[arm > 0].astype(np.float32) * 0.55).astype(np.uint8)
mask4[arm > 0] = 255

out6 = strip_shadow_fragments_from_mask(mask4, before4, after4)
bld_kept = float((out6[40:110, 40:110] > 0).mean())
arm_kept = float((out6[arm > 0] > 0).mean()) if arm.sum() else 0.0
check("building kept when a shadow arm touches it", bld_kept > 0.90, f"{bld_kept:.0%} kept")
# The disputed boundary between building and arm is deliberately resolved in
# the building's favour (a real report showed shaded roof pixels wrongly
# stripped when the tie went the other way) -- most, not all, of the arm
# is expected to go.
check("attached shadow arm mostly removed", arm_kept < 0.45, f"{arm_kept:.0%} kept")

print("=" * 64)
print("DARK BUT TEXTURED - real dark objects must not read as shadow")
print("=" * 64)

# Darkness + low chroma_shift alone match a dark roof, a dark/black vehicle,
# or shaded foliage just as well as an actual shadow -- what actually tells
# them apart is texture. A real object has internal structure (panel lines,
# window/reflection contrast, leaf detail); a cast shadow is a smooth
# brightness multiplier over whatever it falls on. Each case here is as dark
# as a shadow but textured, and must survive; the control has the same
# darkness with zero texture and must not.
_tex_rng = np.random.default_rng(0)


def _textured(img, region, strength):
    noise = _tex_rng.integers(-strength, strength, img[region].shape, dtype=np.int16)
    img[region] = np.clip(img[region].astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _dark_case(color, region, strength):
    b = np.full((H, W, 3), 150, np.uint8)
    _textured(b, np.s_[:, :], 6)
    a = b.copy()
    a[region] = color
    _textured(a, region, strength)
    m = np.zeros((H, W), np.uint8)
    m[region] = 255
    return b, a, m


roof_b, roof_a, roof_m = _dark_case([55, 55, 58], np.s_[60:180, 60:180], 14)
roof_kept = float((strip_shadow_fragments_from_mask(roof_m, roof_b, roof_a)[roof_m > 0] > 0).mean())
check("textured dark roof preserved", roof_kept > 0.90, f"{roof_kept:.0%} kept")

veg_b, veg_a, veg_m = _dark_case([45, 85, 40], np.s_[60:180, 60:180], 20)
veg_kept = float((strip_shadow_fragments_from_mask(veg_m, veg_b, veg_a)[veg_m > 0] > 0).mean())
check("textured dense vegetation preserved", veg_kept > 0.90, f"{veg_kept:.0%} kept")

# Windows/reflections give a real vehicle strong local contrast, not faint speckle.
car_b, car_a, car_m = _dark_case([40, 40, 42], np.s_[130:150, 120:170], 25)
car_kept = float((strip_shadow_fragments_from_mask(car_m, car_b, car_a)[car_m > 0] > 0).mean())
check("textured dark vehicle preserved", car_kept > 0.90, f"{car_kept:.0%} kept")

flat_b = np.full((H, W, 3), 150, np.uint8)
_textured(flat_b, np.s_[:, :], 6)
flat_a = flat_b.copy()
flat_a[60:180, 60:180] = (flat_b[60:180, 60:180].astype(np.float32) * 0.5).astype(np.uint8)
flat_m = np.zeros((H, W), np.uint8)
flat_m[60:180, 60:180] = 255
flat_kept = float((strip_shadow_fragments_from_mask(flat_m, flat_b, flat_a)[flat_m > 0] > 0).mean())
check("smooth flat darkening (true shadow) still removed", flat_kept < 0.10, f"{flat_kept:.0%} kept")

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
