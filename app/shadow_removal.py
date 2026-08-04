"""Shadow detection and removal (relighting) for aerial / drone imagery.

Distinct from the shadow *suppression* in ``detection_engine`` — that only stops
shadow-only differences being reported as change, and needs a before/after pair.
This module works on a **single image**: it finds shadowed pixels and restores
their true surface appearance, so downstream detection compares like with like
even when two dates were flown at different times of day.

Pipeline
--------
1. ``no_data_mask``   — pure-black orthomosaic borders are excluded up front.
   They are not shadows and must never be "restored" (that would fabricate
   image content where the sensor recorded none).
2. ``shadow_index``   — per-pixel likelihood combining darkness with the blue
   shift of skylight-only illumination.
3. ``detect_shadows`` — Otsu seeds + hysteresis so faint/small shadows attached
   to confident ones survive, then a per-component check that separates true
   shadows from genuinely dark objects (dark tarp, asphalt, water).
4. ``remove_shadows`` — per-component linear illumination transfer against the
   surrounding lit ring, feathered across the penumbra so no seams or halos
   remain.

The linear transfer is deliberate: scaling preserves the texture and relative
colour inside the region, where an inpaint/fill would erase the very detail the
detector needs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# A pixel is treated as sensor no-data when every channel is at/near zero.
NO_DATA_MAX = 8
# Shadows rarely exceed this share of a scene; above it the index is likely
# tracking a dark surface (or a night image) rather than cast shadow.
MAX_SHADOW_FRACTION = 0.60


@dataclass
class ShadowResult:
    """Outcome of a shadow detect/remove pass."""
    mask: np.ndarray                      # uint8 0/255, final shadow mask
    index: np.ndarray                     # float32 [0,1] shadow likelihood
    image: Optional[np.ndarray] = None    # relit RGB (None for detect-only)
    shadow_fraction: float = 0.0
    components: int = 0
    rejected_dark_objects: int = 0
    no_data_fraction: float = 0.0
    notes: list = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "shadowFraction": round(float(self.shadow_fraction), 4),
            "components": int(self.components),
            "rejectedDarkObjects": int(self.rejected_dark_objects),
            "noDataFraction": round(float(self.no_data_fraction), 4),
            "notes": list(self.notes),
        }


# ---------------------------------------------------------------------------
# 1. No-data
# ---------------------------------------------------------------------------

def no_data_mask(img: np.ndarray) -> np.ndarray:
    """Pixels the sensor never recorded (black orthomosaic border), as 0/255.

    Rotated orthomosaics are padded with pure black. Those pixels satisfy every
    "is it dark?" test but contain no surface to restore, so they are held out
    of both detection and removal.
    """
    dark_all = np.all(img <= NO_DATA_MAX, axis=2)
    m = (dark_all.astype(np.uint8)) * 255
    # Keep only regions connected to the frame edge; an interior black object
    # (tarp, deep shade) is real image content, not padding.
    h, w = m.shape
    flood = m.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        if flood[seed[1], seed[0]] == 255:
            cv2.floodFill(flood, ff_mask, seed, 128)
    return ((flood == 128).astype(np.uint8)) * 255


# ---------------------------------------------------------------------------
# 2. Shadow likelihood
# ---------------------------------------------------------------------------

def shadow_index(img: np.ndarray) -> np.ndarray:
    """Per-pixel shadow likelihood in [0, 1].

    Combines two independent cues so neither alone dominates:
      * darkness — shadows lose direct sun, so luminance drops;
      * blue shift — a shadowed surface is lit by sky only, which is bluer than
        sunlight, so blue rises *relative* to overall intensity.

    The ratio cue is what separates shadow from an intrinsically dark surface:
    black tarp is dark in every channel, shadowed concrete is dark *and* bluer.
    """
    f = img.astype(np.float32) + 1.0
    intensity = f.mean(axis=2)
    blue_ratio = f[:, :, 2] / intensity          # >1 where blue dominates

    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
    L = lab[:, :, 0].astype(np.float32) / 255.0

    # Normalise against the scene's own lit level (95th percentile) so the cue
    # is exposure-independent: "dark relative to this image", not an absolute.
    lit_level = max(float(np.percentile(L[L > 0.02], 95)) if np.any(L > 0.02) else 1.0, 1e-3)
    darkness = np.clip(1.0 - L / lit_level, 0, 1)
    blueness = np.clip((blue_ratio - 1.0) / 0.25, 0, 1)

    # Weighted toward darkness (the primary signal) with blueness confirming.
    idx = 0.65 * darkness + 0.35 * darkness * blueness
    idx = cv2.GaussianBlur(idx, (0, 0), 1.5)
    return np.clip(idx, 0, 1).astype(np.float32)


# ---------------------------------------------------------------------------
# 3. Detection: seeds -> hysteresis -> dark-object rejection
# ---------------------------------------------------------------------------

def _ring_of(comp: np.ndarray, width: int) -> np.ndarray:
    """Lit collar just outside a component, used as its relighting reference."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (width * 2 + 1,) * 2)
    return cv2.subtract(cv2.dilate(comp, k), comp)


def _is_true_shadow(img: np.ndarray, comp: np.ndarray, ring: np.ndarray) -> bool:
    """Same material, just unlit (shadow) — or a genuinely dark object?

    A shadow keeps its surface's hue and texture and merely loses light, so
    against its lit surround it shows a large luminance drop but a similar
    chromatic character. A dark object differs in *kind*: its colour is dark in
    its own right, and it is often flat (a tarp has little internal texture).
    """
    # Judge the component's *core*: hysteresis + closing pull transition pixels
    # from the lit surround into the blob, and those inflate the apparent
    # texture of an otherwise flat object (a black tarp measured edge-to-edge
    # looks "textured" purely because of its own boundary).
    core = cv2.erode(comp, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    if int((core > 0).sum()) < 24:
        core = comp  # thin/small shape — nothing left to erode to

    comp_px = core > 0
    ring_px = ring > 0
    if comp_px.sum() < 24 or ring_px.sum() < 24:
        return True  # too small to judge; darkness cue already fired

    lab = cv2.cvtColor(img, cv2.COLOR_RGB2LAB).astype(np.float32)
    L, a_, b_ = lab[:, :, 0], lab[:, :, 1], lab[:, :, 2]

    l_comp, l_ring = float(L[comp_px].mean()), float(L[ring_px].mean())
    if l_ring - l_comp < 6.0:
        return False  # barely darker than its surround — not a cast shadow

    # Chromaticity distance: shadow shifts mainly in L, an object also in a*/b*.
    chroma_shift = float(np.hypot(a_[comp_px].mean() - a_[ring_px].mean(),
                                  b_[comp_px].mean() - b_[ring_px].mean()))
    # Texture retention: shadowed ground keeps structure once contrast-normalised.
    tex_comp = float(L[comp_px].std())
    relative_tex = tex_comp / max(float(L[ring_px].std()), 1e-3)

    # Very dark + chromatically distinct + flat => a dark surface, not shadow.
    if l_comp < 45 and chroma_shift > 9.0 and relative_tex < 0.55:
        return False
    # Extremely flat and near-black regardless of chroma (fresh tarp / water).
    if l_comp < 28 and tex_comp < 4.0:
        return False
    return True


def detect_shadows(
    img: np.ndarray,
    *,
    strength: float = 1.0,
    min_area: int = 40,
) -> ShadowResult:
    """Build a shadow mask covering faint, small and irregular shadows.

    ``strength`` (0.5–1.5) scales sensitivity: >1 admits fainter shadows, <1 is
    conservative. Hysteresis keeps weak pixels only where they are contiguous
    with a confident seed, which is what lets soft penumbra and thin shadow
    fingers survive without the threshold also swallowing every dim surface.
    """
    notes: list[str] = []
    nodata = no_data_mask(img)
    valid = nodata == 0
    idx = shadow_index(img)

    scored = idx[valid]
    if scored.size == 0:
        return ShadowResult(mask=np.zeros(img.shape[:2], np.uint8), index=idx,
                            notes=["image is entirely no-data"])

    # Otsu on the valid pixels gives a scene-adaptive seed threshold.
    otsu, _ = cv2.threshold((scored * 255).astype(np.uint8), 0, 255,
                            cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    high = float(np.clip(otsu / 255.0 / max(strength, 1e-3), 0.05, 0.95))
    low = high * 0.72  # penumbra / faint shadow band

    seeds = ((idx >= high) & valid).astype(np.uint8)
    weak = ((idx >= low) & valid).astype(np.uint8)
    if seeds.sum() == 0:
        return ShadowResult(mask=np.zeros(img.shape[:2], np.uint8), index=idx,
                            no_data_fraction=float((nodata > 0).mean()),
                            notes=["no shadow seeds above threshold"])

    # Hysteresis: keep weak components containing at least one seed.
    n, labels = cv2.connectedComponents(weak, connectivity=8)
    keep = np.unique(labels[seeds > 0])
    grown = np.isin(labels, keep[keep > 0]).astype(np.uint8) * 255
    grown = cv2.morphologyEx(
        grown, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))

    # Per-component adjudication (requirement: analyse context before removing).
    n2, lab2, stats, _ = cv2.connectedComponentsWithStats(
        (grown > 0).astype(np.uint8), connectivity=8)
    final = np.zeros(img.shape[:2], np.uint8)
    kept = rejected = 0
    for i in range(1, n2):
        if stats[i, cv2.CC_STAT_AREA] < min_area:
            continue
        comp = ((lab2 == i).astype(np.uint8)) * 255
        ring = cv2.bitwise_and(_ring_of(comp, 9), (valid.astype(np.uint8)) * 255)
        ring = cv2.bitwise_and(ring, cv2.bitwise_not(grown))  # lit reference only
        if _is_true_shadow(img, comp, ring):
            final[comp > 0] = 255
            kept += 1
        else:
            rejected += 1

    frac = float((final > 0).mean())
    if frac > MAX_SHADOW_FRACTION:
        notes.append(
            f"shadow mask covers {frac:.0%} of the frame — likely a dark or "
            "low-light scene rather than cast shadow; lower `strength` if the "
            "result looks over-brightened")
    if rejected:
        notes.append(f"{rejected} dark region(s) kept as objects, not shadows")

    return ShadowResult(mask=final, index=idx, shadow_fraction=frac,
                        components=kept, rejected_dark_objects=rejected,
                        no_data_fraction=float((nodata > 0).mean()), notes=notes)


# ---------------------------------------------------------------------------
# 4. Removal by illumination compensation
# ---------------------------------------------------------------------------

def _feather(mask: np.ndarray, width: int = 9) -> np.ndarray:
    """Soft 0→1 alpha that ramps across the penumbra, avoiding hard seams."""
    dist = cv2.distanceTransform((mask > 0).astype(np.uint8), cv2.DIST_L2, 3)
    alpha = np.clip(dist / max(width, 1), 0, 1)
    return cv2.GaussianBlur(alpha.astype(np.float32), (0, 0), width / 3.0)


def remove_shadows(
    img: np.ndarray,
    result: Optional[ShadowResult] = None,
    *,
    strength: float = 1.0,
    max_gain: float = 3.2,
) -> ShadowResult:
    """Relight shadowed pixels to match their surrounding lit surface.

    Each component is corrected independently against its own lit ring, because
    a shadow on grass and a shadow on concrete need different corrections. The
    transfer matches both mean and spread per channel, so texture and colour
    survive; ``max_gain`` caps amplification so deep shade turns into plausible
    surface rather than amplified sensor noise.
    """
    res = result if result is not None else detect_shadows(img, strength=strength)
    out = img.astype(np.float32).copy()
    if res.mask is None or not np.any(res.mask):
        res.image = img.copy()
        return res

    nodata = no_data_mask(img)
    lit_ref = cv2.bitwise_and(cv2.bitwise_not(res.mask), cv2.bitwise_not(nodata))

    n, labels, stats, _ = cv2.connectedComponentsWithStats(
        (res.mask > 0).astype(np.uint8), connectivity=8)
    corrected = 0
    for i in range(1, n):
        area = stats[i, cv2.CC_STAT_AREA]
        if area < 12:
            continue
        comp = ((labels == i).astype(np.uint8)) * 255
        # Widen the search until the ring holds enough lit reference pixels.
        ring = None
        for width in (9, 15, 24, 36):
            cand = cv2.bitwise_and(_ring_of(comp, width), lit_ref)
            if int((cand > 0).sum()) >= max(40, area // 12):
                ring = cand
                break
        if ring is None or not np.any(ring):
            continue

        cpx, rpx = comp > 0, ring > 0
        alpha = _feather(comp)[..., None]
        for c in range(3):
            ch = out[:, :, c]
            m_s, s_s = float(ch[cpx].mean()), float(ch[cpx].std())
            m_l, s_l = float(ch[rpx].mean()), float(ch[rpx].std())
            # Losing light compresses a surface's contrast, so restoring it can
            # only expand: never allow gain < 1, which would flatten the very
            # texture the correction is meant to bring back. (A component
            # spanning several materials can otherwise out-vary its own lit
            # ring and get squashed.)
            gain = float(np.clip(s_l / max(s_s, 1.0), 1.0, max_gain))
            adjusted = (ch - m_s) * gain + m_l
            # Feathered blend: full correction in the umbra, easing to none at
            # the boundary so the penumbra transitions without a visible edge.
            ch[:] = ch * (1 - alpha[..., 0]) + adjusted * alpha[..., 0]
        corrected += 1

    out = np.clip(out, 0, 255).astype(np.uint8)
    out[nodata > 0] = img[nodata > 0]  # never invent content in no-data padding
    res.image = out
    res.notes.append(f"relit {corrected} shadow component(s)")
    return res


def remove_shadows_from_image(img: np.ndarray, *, strength: float = 1.0):
    """Convenience one-shot: returns ``(relit_image, ShadowResult)``."""
    res = remove_shadows(img, strength=strength)
    return res.image, res


def _main(argv=None) -> int:
    import argparse

    from PIL import Image

    ap = argparse.ArgumentParser(description="Detect and remove shadows in an image.")
    ap.add_argument("image")
    ap.add_argument("--out", help="write the relit image here")
    ap.add_argument("--mask-out", help="write the shadow mask here")
    ap.add_argument("--strength", type=float, default=1.0,
                    help="0.5 conservative … 1.5 aggressive (default 1.0)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    img = np.array(Image.open(args.image).convert("RGB"))
    relit, res = remove_shadows_from_image(img, strength=args.strength)

    print(f"shadow fraction : {res.shadow_fraction:.1%}")
    print(f"components      : {res.components}")
    print(f"dark objects kept: {res.rejected_dark_objects}")
    print(f"no-data         : {res.no_data_fraction:.1%}")
    for note in res.notes:
        print(f"note            : {note}")
    if args.mask_out:
        Image.fromarray(res.mask).save(args.mask_out)
        print(f"wrote {args.mask_out}")
    if args.out:
        Image.fromarray(relit).save(args.out)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
