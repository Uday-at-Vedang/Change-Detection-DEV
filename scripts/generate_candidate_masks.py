"""
Day 2: generate candidate ground-truth change masks for every pair in
docs/delhi_eval/manifest.json, as a starting point for hand-review — NOT a
substitute for it. Unsupervised Change Vector Analysis (pixel-wise RGB
distance) + Otsu thresholding + light morphological cleanup.

These candidates WILL contain false signal (illumination differences, sensor
noise, cloud edges) alongside real change. Review each one before promoting
it to a real ground-truth label — see scripts/accept_candidate_mask.py.

Usage:
    python scripts/generate_candidate_masks.py
    python scripts/generate_candidate_masks.py --pair-id delhi_0001
"""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "delhi_eval" / "manifest.json"
CANDIDATES_DIR = ROOT / "docs" / "delhi_eval" / "labels" / "candidates"
PREVIEWS_DIR = ROOT / "docs" / "delhi_eval" / "labels" / "candidates" / "previews"


def _load_rgb(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".tif", ".tiff"):
        import rasterio
        with rasterio.open(path) as ds:
            return np.transpose(ds.read([1, 2, 3]), (1, 2, 0))
    return np.array(Image.open(path).convert("RGB"))


def cva_otsu_mask(before: np.ndarray, after: np.ndarray,
                   percentile: float = 92.0, min_blob_px: int = 40) -> np.ndarray:
    """Pixel-wise change magnitude (Euclidean RGB distance) -> conservative
    top-percentile threshold (Otsu was splitting near the noise floor and
    flagging 20-35% of every scene as 'changed' on this agricultural-heavy
    imagery — a percentile cut is stricter) -> morphological cleanup ->
    connected-component area filter to drop small speckle (crop-texture
    noise tends to be small scattered flecks; real structural change tends
    to be a solid contiguous blob)."""
    diff = before.astype(np.float32) - after.astype(np.float32)
    magnitude = np.sqrt(np.sum(diff ** 2, axis=-1))

    threshold_value = np.percentile(magnitude, percentile)
    mask = (magnitude >= threshold_value).astype(np.uint8) * 255

    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    cleaned = np.zeros_like(mask)
    for label in range(1, num_labels):  # 0 = background
        if stats[label, cv2.CC_STAT_AREA] >= min_blob_px:
            cleaned[labels == label] = 255
    return cleaned


def _save_preview(pair_id, before, after, mask, out_dir):
    overlay = after.copy()
    overlay[mask > 0] = (0.4 * overlay[mask > 0] + 0.6 * np.array([255, 40, 40])).astype(np.uint8)
    strip = np.concatenate([before, after, np.dstack([mask] * 3), overlay], axis=1)
    Image.fromarray(strip).save(out_dir / f"{pair_id}.png")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pair-id", default="", help="only process this one pair (default: all)")
    args = parser.parse_args()

    CANDIDATES_DIR.mkdir(parents=True, exist_ok=True)
    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    pairs = manifest["pairs"]
    if args.pair_id:
        pairs = [p for p in pairs if p["pair_id"] == args.pair_id]
        if not pairs:
            raise SystemExit(f"No such pair_id: {args.pair_id}")

    print(f"Generating candidate masks for {len(pairs)} pair(s)...\n")
    for pair in pairs:
        pair_id = pair["pair_id"]
        before = _load_rgb(ROOT / pair["before_path"])
        after = _load_rgb(ROOT / pair["after_path"])
        mask = cva_otsu_mask(before, after)

        candidate_path = CANDIDATES_DIR / f"{pair_id}.png"
        Image.fromarray(mask).save(candidate_path)
        _save_preview(pair_id, before, after, mask, PREVIEWS_DIR)

        change_pct = 100.0 * np.mean(mask > 0)
        print(f"  {pair_id}  change%={change_pct:5.1f}  -> {candidate_path.relative_to(ROOT)}"
              f"  (preview: {(PREVIEWS_DIR / f'{pair_id}.png').relative_to(ROOT)})")

    print(f"\nReview previews in {PREVIEWS_DIR.relative_to(ROOT)}/ (before | after | mask | overlay strips).")
    print("Then promote good ones with: python scripts/accept_candidate_mask.py --pair-id <id>")


if __name__ == "__main__":
    main()
