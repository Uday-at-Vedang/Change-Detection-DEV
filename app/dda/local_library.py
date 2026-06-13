"""
Read satellite/drone images from local year-based folders.

Folder layout (under library_sources/):

    library_sources/
      2024/
        image_a.tif
        site_1/
          image_b.tif
      2025/
        image_c.tif

Copy or save files directly into the year folder on disk; the app scans on load / refresh.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException

from .config import ALLOWED_EXTENSIONS, LOCAL_LIBRARY_ROOT, LOCAL_THUMB_CACHE
from .geotiff_io import inspect_image, raster_to_preview_png

logger = logging.getLogger(__name__)


@dataclass
class LocalImageEntry:
    path: str  # relative posix path, e.g. 2025/aerial.tif
    year: int
    filename: str
    file_size_bytes: int


def _is_year_dir(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1990 <= int(name) <= 2100


def safe_resolve(relative_path: str) -> Path:
    """Resolve a library-relative path; block path traversal."""
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="Invalid image path")
    root = LOCAL_LIBRARY_ROOT.resolve()
    full = (root / rel).resolve()
    try:
        full.relative_to(root)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid image path")
    if not full.is_file():
        raise HTTPException(status_code=404, detail="Image file not found")
    if full.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Unsupported image format")
    return full


def scan_years() -> List[dict]:
    """List year folders and image counts."""
    ensure_root()
    years = []
    if not LOCAL_LIBRARY_ROOT.exists():
        return years
    for entry in sorted(LOCAL_LIBRARY_ROOT.iterdir()):
        if not entry.is_dir() or not _is_year_dir(entry.name):
            continue
        count = sum(
            1
            for p in entry.rglob("*")
            if p.is_file() and p.suffix.lower() in ALLOWED_EXTENSIONS
        )
        years.append({"year": int(entry.name), "imageCount": count})
    return years


def scan_images(year: Optional[int] = None, query: Optional[str] = None) -> List[LocalImageEntry]:
    """Scan library_sources for images, optionally filtered by year and filename."""
    ensure_root()
    results: List[LocalImageEntry] = []
    root = LOCAL_LIBRARY_ROOT
    if not root.exists():
        return results

    q = (query or "").strip().lower()
    year_dirs: List[Path]
    if year is not None:
        ydir = root / str(year)
        year_dirs = [ydir] if ydir.is_dir() else []
    else:
        year_dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and _is_year_dir(d.name)]

    for ydir in year_dirs:
        y = int(ydir.name)
        for path in sorted(ydir.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue
            rel = path.relative_to(root).as_posix()
            if q and q not in rel.lower():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            results.append(
                LocalImageEntry(
                    path=rel,
                    year=y,
                    filename=path.name,
                    file_size_bytes=size,
                )
            )
    return results


def entry_to_dict(entry: LocalImageEntry, include_meta: bool = False) -> dict:
    out = {
        "path": entry.path,
        "year": entry.year,
        "filename": entry.filename,
        "fileSizeBytes": entry.file_size_bytes,
        "thumbUrl": f"/api/dda/local/thumb?path={entry.path}",
        "source": "local_folder",
    }
    if include_meta:
        try:
            full = safe_resolve(entry.path)
            meta = inspect_image(full)
            out.update({
                "width": meta.width,
                "height": meta.height,
                "hasGeoref": meta.has_georef,
                "format": meta.format,
            })
        except Exception as exc:
            logger.warning("Metadata read failed for %s: %s", entry.path, exc)
    return out


def thumb_cache_path(relative_path: str) -> Path:
    key = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:32]
    return LOCAL_THUMB_CACHE / f"{key}.png"


def get_or_build_thumb(relative_path: str, max_side: int = 256) -> Path:
    full = safe_resolve(relative_path)
    cache = thumb_cache_path(relative_path)
    if cache.exists():
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    raster_to_preview_png(full, cache, max_side=max_side)
    return cache


def ensure_root() -> None:
    LOCAL_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)
    LOCAL_THUMB_CACHE.mkdir(parents=True, exist_ok=True)
