"""Read-only Delhi evaluation loader — Priyanka's manifest schema.

See ``docs/delhi_eval/README.md``. Paths in the manifest are relative to the
repo root (not the eval folder). Uday scripts only read; never write eval data.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_MANIFEST = ROOT / "docs" / "delhi_eval" / "manifest.json"
LABELS_DIR = ROOT / "docs" / "delhi_eval" / "labels"


class DelhiEvalNotReady(Exception):
    """Manifest missing or unreadable."""


def manifest_path(path: str | Path | None = None) -> Path:
    return Path(path).resolve() if path else DEFAULT_MANIFEST


def load_manifest(path: str | Path | None = None, *, required: bool = True) -> dict:
    mpath = manifest_path(path)
    if not mpath.is_file():
        if required:
            raise DelhiEvalNotReady(
                f"Delhi manifest not found at {mpath}. "
                "Merge New/Priyanka or run scripts/build_delhi_manifest.py --init."
            )
        return {"pairs": []}
    return json.loads(mpath.read_text(encoding="utf-8"))


def _resolve_gt_path(pair: dict) -> Path | None:
    gt_rel = pair.get("gt_mask")
    if gt_rel:
        p = ROOT / gt_rel
        return p if p.is_file() else None
    pair_id = pair.get("pair_id") or pair.get("id")
    if pair_id:
        auto = LABELS_DIR / f"{pair_id}.png"
        if auto.is_file():
            return auto
    return None


def _load_rgb(path: Path) -> np.ndarray:
    if path.suffix.lower() in (".tif", ".tiff"):
        from app.dda.geotiff_io import load_rgb_pil
        return np.array(load_rgb_pil(path))
    return np.array(Image.open(path).convert("RGB"))


def _load_label(path: Path) -> np.ndarray:
    return np.array(Image.open(path).convert("L"))


def iter_delhi_pairs(
    manifest: str | Path | None = None,
    *,
    require_gt: bool = False,
) -> Iterator[tuple[np.ndarray, np.ndarray, np.ndarray | None, str, str | None, str | None]]:
    """Yield (before, after, gt_or_none, pair_id, before_path, after_path)."""
    data = load_manifest(manifest, required=True)
    missing: list[str] = []

    for pair in data.get("pairs", []):
        pair_id = pair.get("pair_id") or pair.get("id") or "unknown"
        before_rel = pair.get("before_path") or pair.get("before")
        after_rel = pair.get("after_path") or pair.get("after")
        if not (before_rel and after_rel):
            missing.append(f"{pair_id}: missing before/after paths")
            continue

        before_p = ROOT / before_rel
        after_p = ROOT / after_rel
        if not before_p.is_file() or not after_p.is_file():
            missing.append(f"{pair_id}: image missing on disk")
            continue

        try:
            before = _load_rgb(before_p)
            after = _load_rgb(after_p)
        except Exception as exc:
            missing.append(f"{pair_id}: load failed ({exc})")
            continue

        gt = None
        gt_path = _resolve_gt_path(pair)
        if gt_path is not None:
            gt = _load_label(gt_path)
        elif require_gt:
            continue

        is_tif = before_p.suffix.lower() in (".tif", ".tiff")
        bp = str(before_p) if is_tif else None
        ap = str(after_p) if is_tif else None
        yield before, after, gt, pair_id, bp, ap

    if missing:
        print(f"  WARNING: skipped {len(missing)} manifest entries:")
        for msg in missing[:8]:
            print(f"    - {msg}")
        if len(missing) > 8:
            print(f"    ... and {len(missing) - 8} more")


def count_manifest_pairs(manifest: str | Path | None = None) -> dict:
    data = load_manifest(manifest, required=True)
    pairs = data.get("pairs", [])
    on_disk = sum(
        1 for p in pairs
        if (ROOT / p["before_path"]).is_file() and (ROOT / p["after_path"]).is_file()
    )
    labeled = sum(1 for p in pairs if _resolve_gt_path(p) is not None)
    return {"total": len(pairs), "on_disk": on_disk, "labeled": labeled}


def dummy_delhi_pairs(n: int = 2, size: int = 384) -> list[tuple]:
    """In-memory synthetic pairs for Uday scaffold runs."""
    rng = np.random.default_rng(42)
    out = []
    for i in range(n):
        before = rng.integers(40, 200, (size, size, 3), dtype=np.uint8)
        before[:, size // 3: size // 3 + 6] = [90, 90, 90]
        after = before.copy()
        gt = np.zeros((size, size), dtype=np.uint8)
        x, y, w, h = 60 + i * 40, 70 + i * 20, 50, 40
        after[y:y + h, x:x + w] = [205, 200, 190]
        gt[y:y + h, x:x + w] = 255
        out.append((before, after, gt, f"dummy_{i:02d}", None, None))
    return out
