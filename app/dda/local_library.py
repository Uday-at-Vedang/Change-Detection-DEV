"""
Read satellite/drone images from local year-based folders.

Folder layout (under library_sources/):

    library_sources/
      2024/
        image_a.tif
      2025/
        image_c.tif

Copy files into library_sources/YEAR/ in the project folder (or data/library_sources/ on HF).
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import HTTPException

from .config import ALLOWED_EXTENSIONS, LOCAL_THUMB_CACHE, get_library_roots
from .geotiff_io import inspect_image, raster_to_preview_png

logger = logging.getLogger(__name__)


@dataclass
class LocalImageEntry:
    path: str  # relative posix path, e.g. 2025/aerial.tif
    root: Path
    year: int
    filename: str
    file_size_bytes: int


def _is_year_dir(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1990 <= int(name) <= 2100


def _iter_image_files(root: Path, year: Optional[int] = None):
    if not root.exists():
        return
    if year is not None:
        year_dirs = [root / str(year)] if (root / str(year)).is_dir() else []
    else:
        year_dirs = [d for d in sorted(root.iterdir()) if d.is_dir() and _is_year_dir(d.name)]
    for ydir in year_dirs:
        y = int(ydir.name)
        for path in sorted(ydir.rglob("*")):
            if not path.is_file():
                continue
            ext = path.suffix.lower()
            if ext not in ALLOWED_EXTENSIONS:
                logger.debug("Skipped (extension %s): %s", ext, path)
                continue
            yield root, y, path


def safe_resolve(relative_path: str) -> Path:
    """Resolve a library-relative path across all configured roots."""
    rel = relative_path.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="Invalid image path")
    for root in get_library_roots():
        full = (root / rel).resolve()
        try:
            full.relative_to(root.resolve())
        except ValueError:
            continue
        if full.is_file() and full.suffix.lower() in ALLOWED_EXTENSIONS:
            return full
    raise HTTPException(status_code=404, detail="Image file not found")


def scan_years() -> List[dict]:
    """List year folders and image counts (merged across all roots)."""
    ensure_root()
    counts: dict[int, int] = {}
    for root in get_library_roots():
        if not root.exists():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not _is_year_dir(entry.name):
                continue
            y = int(entry.name)
            n = sum(1 for _ in _iter_image_files(root, year=y))
            counts[y] = counts.get(y, 0) + n
    return [{"year": y, "imageCount": counts[y]} for y in sorted(counts)]


def scan_images(year: Optional[int] = None, query: Optional[str] = None) -> List[LocalImageEntry]:
    """Scan all library roots for images."""
    ensure_root()
    results: List[LocalImageEntry] = []
    seen_paths: set[str] = set()
    q = (query or "").strip().lower()

    for root in get_library_roots():
        for r, y, path in _iter_image_files(root, year=year):
            rel = path.relative_to(r).as_posix()
            if rel in seen_paths:
                continue
            if q and q not in rel.lower():
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            seen_paths.add(rel)
            results.append(
                LocalImageEntry(
                    path=rel,
                    root=r,
                    year=y,
                    filename=path.name,
                    file_size_bytes=size,
                )
            )
    return results


def entry_to_dict(entry: LocalImageEntry, include_meta: bool = False) -> dict:
    encoded_path = quote(entry.path, safe="/")
    out = {
        "path": entry.path,
        "year": entry.year,
        "filename": entry.filename,
        "fileSizeBytes": entry.file_size_bytes,
        "thumbUrl": f"/api/dda/local/thumb?path={encoded_path}",
        "source": "local_folder",
        "rootPath": str(entry.root),
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
    if cache.exists() and cache.stat().st_mtime >= full.stat().st_mtime:
        return cache
    cache.parent.mkdir(parents=True, exist_ok=True)
    raster_to_preview_png(full, cache, max_side=max_side)
    return cache


def ensure_root() -> None:
    for root in get_library_roots():
        root.mkdir(parents=True, exist_ok=True)
    LOCAL_THUMB_CACHE.mkdir(parents=True, exist_ok=True)


def library_debug_info() -> dict:
    """Diagnostics for troubleshooting missing images."""
    roots_info = []
    for root in get_library_roots():
        info = {
            "path": str(root),
            "exists": root.exists(),
            "years": [],
            "otherFiles": [],
        }
        if root.exists():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and _is_year_dir(entry.name):
                    files = []
                    for _, _, p in _iter_image_files(root, year=int(entry.name)):
                        files.append({"name": p.name, "size": p.stat().st_size})
                    info["years"].append({"year": entry.name, "files": files})
                elif entry.is_file() and entry.name not in ("README.md", ".gitkeep"):
                    info["otherFiles"].append(entry.name)
        roots_info.append(info)
    return {
        "roots": roots_info,
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "totalImages": len(scan_images()),
    }
