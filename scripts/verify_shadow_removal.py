"""Verify shadow detection/removal against the task requirements.

Measures, rather than eyeballs, the properties that matter:
  * non-shadow pixels are left alone            (requirement 7)
  * shadowed pixels are actually lifted          (requirement 5)
  * no residual dark patches inside the mask     (requirement 6)
  * no hard seams / halos at shadow boundaries   (requirement 6)
  * genuinely dark objects survive               (requirement 8)
  * no-data padding is never invented            (safety)

Run:  venv\\Scripts\\python scripts\\verify_shadow_removal.py <image> [more images...]
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cv2
import numpy as np
from PIL import Image

from app.shadow_removal import (
    detect_shadows, no_data_mask, remove_shadows, shadow_index,
)

_fails: list[str] = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails.append(name)


def luminance(img):
    return cv2.cvtColor(img, cv2.COLOR_RGB2LAB)[:, :, 0].astype(np.float32)


def evaluate(path: Path) -> None:
    print("=" * 66)
    print(f"IMAGE: {path.name}")
    print("=" * 66)
    img = np.array(Image.open(path).convert("RGB"))
    res = remove_shadows(img)
    out = res.image
    mask = res.mask > 0
    nodata = no_data_mask(img) > 0
    lit = (~mask) & (~nodata)

    print(f"  shadow={res.shadow_fraction:.1%}  components={res.components}  "
          f"dark-objects-kept={res.rejected_dark_objects}  no-data={res.no_data_fraction:.1%}")

    L_in, L_out = luminance(img), luminance(out)

    # (7) Non-shadow regions untouched. Feathering intentionally eases the
    # correction across the boundary, so judge pixels away from the edge.
    edge = cv2.dilate((mask.astype(np.uint8)) * 255,
                      cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))) > 0
    interior_lit = lit & (~edge)
    if interior_lit.sum() > 100:
        delta = float(np.abs(L_out[interior_lit] - L_in[interior_lit]).mean())
        check("non-shadow regions preserved", delta < 1.0, f"mean |dL| = {delta:.3f}")
    else:
        print("  [SKIP] not enough interior lit pixels to test preservation")

    # (5) Shadowed pixels are genuinely lifted toward the lit level.
    if mask.sum() > 100 and lit.sum() > 100:
        before_gap = float(L_in[lit].mean() - L_in[mask].mean())
        after_gap = float(L_out[lit].mean() - L_out[mask].mean())
        check("shadow/lit luminance gap reduced", after_gap < before_gap,
              f"{before_gap:.1f} -> {after_gap:.1f}")
        check("shadowed pixels brightened",
              float(L_out[mask].mean()) > float(L_in[mask].mean()),
              f"L {L_in[mask].mean():.1f} -> {L_out[mask].mean():.1f}")

        # (6) No residual dark patch left inside what we called shadow.
        very_dark_before = float((L_in[mask] < 40).mean())
        very_dark_after = float((L_out[mask] < 40).mean())
        check("residual dark patches reduced", very_dark_after <= very_dark_before,
              f"{very_dark_before:.1%} -> {very_dark_after:.1%}")

    # (5) Texture must survive — a fill/inpaint would flatten it.
    if mask.sum() > 500:
        t_in = float(L_in[mask].std())
        t_out = float(L_out[mask].std())
        check("texture retained in relit regions", t_out > t_in * 0.8,
              f"std {t_in:.1f} -> {t_out:.1f}")

    # (6) No hard seam: the correction must not create a step edge that wasn't
    # in the source. Compare gradient energy along the mask boundary.
    boundary = (cv2.morphologyEx((mask.astype(np.uint8)) * 255, cv2.MORPH_GRADIENT,
                                 cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))) > 0)
    if boundary.sum() > 100:
        g_in = float(np.abs(cv2.Laplacian(L_in, cv2.CV_32F))[boundary].mean())
        g_out = float(np.abs(cv2.Laplacian(L_out, cv2.CV_32F))[boundary].mean())
        check("no hard seam introduced at boundary", g_out < g_in * 1.6 + 2.0,
              f"edge energy {g_in:.1f} -> {g_out:.1f}")

    # Safety: no-data padding must be byte-identical (never fabricate content).
    if nodata.sum() > 0:
        check("no-data padding untouched",
              np.array_equal(out[nodata], img[nodata]),
              f"{res.no_data_fraction:.1%} of frame")

    # Output must remain a valid image.
    check("output is valid uint8 RGB",
          out.dtype == np.uint8 and out.shape == img.shape)
    for note in res.notes:
        print(f"  note: {note}")


def synthetic_checks() -> None:
    """Controlled cases where the right answer is known."""
    print("=" * 66)
    print("SYNTHETIC - known-answer cases")
    print("=" * 66)
    rng = np.random.default_rng(0)

    # Textured surface, right half shadowed (darker + bluer, as skylight is).
    base = rng.integers(110, 150, (200, 200, 3), dtype=np.uint8)
    shadowed = base.copy()
    region = np.s_[:, 100:]
    shadowed[region] = np.clip(
        base[region].astype(np.float32) * [0.42, 0.46, 0.62], 0, 255).astype(np.uint8)

    res = remove_shadows(shadowed)
    m = res.mask > 0
    right_cov = float(m[:, 120:].mean())
    left_cov = float(m[:, :80].mean())
    check("synthetic shadow detected", right_cov > 0.5, f"{right_cov:.0%} of shadowed half")
    check("lit half not flagged", left_cov < 0.2, f"{left_cov:.0%} of lit half")

    L_in = luminance(shadowed)
    L_out = luminance(res.image)
    if m.sum() > 0:
        check("synthetic shadow lifted",
              float(L_out[m].mean()) > float(L_in[m].mean()) + 10,
              f"L {L_in[m].mean():.0f} -> {L_out[m].mean():.0f}")

    # A dark object on a lit field must NOT be relit away (requirement 8).
    field = np.full((200, 200, 3), 150, np.uint8)
    field += rng.integers(-6, 6, field.shape, dtype=np.int16).astype(np.uint8)
    obj = field.copy()
    obj[80:120, 80:120] = [18, 18, 20]          # flat, near-black, neutral
    res2 = detect_shadows(obj)
    covered = float((res2.mask[85:115, 85:115] > 0).mean())
    check("flat dark object not classified as shadow", covered < 0.5,
          f"{covered:.0%} of object covered")

    # No-data border must be excluded from the shadow mask entirely.
    padded = np.zeros((200, 200, 3), np.uint8)
    padded[40:160, 40:160] = rng.integers(90, 130, (120, 120, 3), dtype=np.uint8)
    nd = no_data_mask(padded) > 0
    res3 = detect_shadows(padded)
    check("no-data border excluded from shadow mask",
          not np.any(res3.mask[nd]), f"border = {nd.mean():.0%} of frame")


def main() -> int:
    paths = [Path(p) for p in sys.argv[1:]]
    synthetic_checks()
    for p in paths:
        if p.is_file():
            evaluate(p)
        else:
            print(f"  [SKIP] missing: {p}")

    print("=" * 66)
    if _fails:
        print(f"RESULT: {len(_fails)} CHECK(S) FAILED -> {', '.join(_fails)}")
        return 1
    print("RESULT: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
