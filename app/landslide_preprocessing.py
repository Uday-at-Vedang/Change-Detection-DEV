"""
Dataset preprocessing and feature extraction starter for landslide modeling.

Usage example:
  python -m app.landslide_preprocessing --pairs_dir data/landslide_pairs --out_csv data/landslide_features.csv

Expected pairs_dir structure:
  pairs_dir/
    event_001/
      before.png
      after.png
      label.png   # optional (binary mask)
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


def _norm01(x: np.ndarray) -> np.ndarray:
    x = x.astype(np.float32)
    lo = float(np.min(x))
    hi = float(np.max(x))
    if hi - lo < 1e-8:
        return np.zeros_like(x, dtype=np.float32)
    return (x - lo) / (hi - lo)


def _green_index(rgb: np.ndarray) -> np.ndarray:
    r = rgb[:, :, 0].astype(np.float32)
    g = rgb[:, :, 1].astype(np.float32)
    return (g - r) / (g + r + 1e-6)


def _soil_score(rgb: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV).astype(np.float32)
    h = hsv[:, :, 0]
    s = hsv[:, :, 1] / 255.0
    v = hsv[:, :, 2] / 255.0
    warm = ((h >= 8) & (h <= 38)).astype(np.float32)
    sat = np.clip(1.0 - np.abs(s - 0.45) / 0.45, 0, 1)
    bri = np.clip((v - 0.25) / 0.75, 0, 1)
    return _norm01(0.5 * warm + 0.25 * sat + 0.25 * bri)


def _texture(gray: np.ndarray) -> np.ndarray:
    lap = cv2.Laplacian(gray.astype(np.float32), cv2.CV_32F, ksize=3)
    return _norm01(cv2.GaussianBlur(np.abs(lap), (5, 5), 0))


def _chip_stats(chip: np.ndarray) -> tuple[float, float, float]:
    return float(np.mean(chip)), float(np.std(chip)), float(np.quantile(chip, 0.9))


def extract_pair_features(before_rgb: np.ndarray, after_rgb: np.ndarray, chip: int = 64):
    if before_rgb.shape != after_rgb.shape:
        after_rgb = cv2.resize(after_rgb, (before_rgb.shape[1], before_rgb.shape[0]))

    g_before = _green_index(before_rgb)
    g_after = _green_index(after_rgb)
    veg_loss = _norm01(np.clip(g_before - g_after, 0, None))

    soil_before = _soil_score(before_rgb)
    soil_after = _soil_score(after_rgb)
    soil_gain = _norm01(np.clip(soil_after - soil_before, 0, None))

    gray_before = cv2.cvtColor(before_rgb, cv2.COLOR_RGB2GRAY)
    gray_after = cv2.cvtColor(after_rgb, cv2.COLOR_RGB2GRAY)
    tex_before = _texture(gray_before)
    tex_after = _texture(gray_after)
    tex_delta = _norm01(np.abs(tex_after - tex_before))

    h, w = veg_loss.shape
    rows = []
    for y in range(0, h - chip + 1, chip):
        for x in range(0, w - chip + 1, chip):
            v = veg_loss[y:y + chip, x:x + chip]
            s = soil_gain[y:y + chip, x:x + chip]
            t = tex_delta[y:y + chip, x:x + chip]
            v_m, v_sd, v_q = _chip_stats(v)
            s_m, s_sd, s_q = _chip_stats(s)
            t_m, t_sd, t_q = _chip_stats(t)
            rows.append({
                "x": x, "y": y,
                "veg_loss_mean": v_m, "veg_loss_std": v_sd, "veg_loss_q90": v_q,
                "soil_gain_mean": s_m, "soil_gain_std": s_sd, "soil_gain_q90": s_q,
                "tex_delta_mean": t_m, "tex_delta_std": t_sd, "tex_delta_q90": t_q,
            })
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pairs_dir", required=True, help="Directory containing event folders with before/after images.")
    parser.add_argument("--out_csv", required=True, help="Output CSV path.")
    parser.add_argument("--chip", type=int, default=64, help="Chip size for feature aggregation.")
    args = parser.parse_args()

    pairs_dir = Path(args.pairs_dir)
    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    for event_dir in sorted([p for p in pairs_dir.iterdir() if p.is_dir()]):
        before_path = event_dir / "before.png"
        after_path = event_dir / "after.png"
        if not before_path.exists() or not after_path.exists():
            continue
        before = np.array(Image.open(before_path).convert("RGB"))
        after = np.array(Image.open(after_path).convert("RGB"))
        rows = extract_pair_features(before, after, chip=args.chip)
        for r in rows:
            r["event_id"] = event_dir.name
        all_rows.extend(rows)

    if not all_rows:
        print("No valid before/after pairs found.")
        return

    fieldnames = list(all_rows[0].keys())
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Wrote {len(all_rows)} rows to {out_csv}")


if __name__ == "__main__":
    main()

