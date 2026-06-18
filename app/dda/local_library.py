"""
Read satellite/drone images from zone/folder/year hierarchy under library_sources/.

Canonical layout:

    library_sources/
      central_delhi/
        site_a/
          2024/
            image_a.tif
      _unassigned/
        legacy/
          2025/
            old_image.tif

Legacy flat library_sources/YEAR/ is still scanned (tagged legacy=true) until migrated.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import ALLOWED_EXTENSIONS, LOCAL_THUMB_CACHE, get_library_roots
from .geotiff_io import inspect_image, raster_to_preview_png, write_placeholder_png
from .library_migration import LEGACY_FOLDER_SLUG, LEGACY_ZONE_SLUG
from .models import DdaVillage, DdaZone

logger = logging.getLogger(__name__)


@dataclass
class SlugLookup:
    zone_by_slug: Dict[str, DdaZone] = field(default_factory=dict)
    folder_by_zone_slug: Dict[str, Dict[str, DdaVillage]] = field(default_factory=dict)
    zone_by_id: Dict[int, DdaZone] = field(default_factory=dict)
    folder_by_id: Dict[int, DdaVillage] = field(default_factory=dict)


@dataclass
class LocalImageEntry:
    path: str
    root: Path
    year: int
    filename: str
    file_size_bytes: int
    zone_id: Optional[int] = None
    zone_name: str = ""
    folder_id: Optional[int] = None
    folder_name: str = ""
    legacy: bool = False


def _is_year_dir(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1990 <= int(name) <= 2100


def build_slug_lookup(db: Session) -> SlugLookup:
    lookup = SlugLookup()
    for zone in db.query(DdaZone).filter(DdaZone.slug.isnot(None)).all():
        lookup.zone_by_slug[zone.slug] = zone
        lookup.zone_by_id[zone.id] = zone
        lookup.folder_by_zone_slug[zone.slug] = {}
    for folder in db.query(DdaVillage).filter(DdaVillage.slug.isnot(None)).all():
        zone = lookup.zone_by_id.get(folder.zone_id)
        if zone and zone.slug:
            lookup.folder_by_zone_slug.setdefault(zone.slug, {})[folder.slug] = folder
        lookup.folder_by_id[folder.id] = folder
    return lookup


def build_upload_dest(root: Path, zone_slug: str, folder_slug: str, year: int, filename: str) -> Path:
    dest_dir = root / zone_slug / folder_slug / str(year)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / filename
    if dest.exists():
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1
    return dest


def parse_library_path(rel_path: str, lookup: SlugLookup) -> Tuple[Optional[int], Optional[int], bool]:
    """Return (zone_id, folder_id, legacy) from relative path."""
    parts = rel_path.replace("\\", "/").split("/")
    if len(parts) >= 4 and _is_year_dir(parts[2]):
        zone_slug, folder_slug = parts[0], parts[1]
        zone = lookup.zone_by_slug.get(zone_slug)
        folder = lookup.folder_by_zone_slug.get(zone_slug, {}).get(folder_slug)
        if zone and folder:
            return zone.id, folder.id, zone_slug == LEGACY_ZONE_SLUG
    if len(parts) == 2 and _is_year_dir(parts[0]):
        return None, None, True
    return None, None, False


def _yield_files_in_year_dir(root: Path, year_dir: Path, rel_prefix: str, year: int,
                             zone_id: Optional[int], zone_name: str,
                             folder_id: Optional[int], folder_name: str, legacy: bool):
    for path in sorted(year_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_EXTENSIONS:
            continue
        rel = f"{rel_prefix}/{path.relative_to(year_dir).as_posix()}".lstrip("/")
        yield root, year, path, rel, zone_id, zone_name, folder_id, folder_name, legacy


def _iter_all_images(root: Path, lookup: SlugLookup, year_filter: Optional[int] = None):
    if not root.exists():
        return

    for zone_dir in sorted(root.iterdir()):
        if not zone_dir.is_dir():
            continue

        # Legacy flat: library_sources/2025/file.tif
        if _is_year_dir(zone_dir.name):
            y = int(zone_dir.name)
            if year_filter is not None and y != year_filter:
                continue
            prefix = zone_dir.name
            for item in _yield_files_in_year_dir(
                root, zone_dir, prefix, y, None, "Unassigned", None, "Legacy", True
            ):
                yield item
            continue

        zone_slug = zone_dir.name
        zone = lookup.zone_by_slug.get(zone_slug)
        zone_name = zone.name if zone else zone_slug
        zone_id = zone.id if zone else None
        folders_map = lookup.folder_by_zone_slug.get(zone_slug, {})

        for folder_dir in sorted(zone_dir.iterdir()):
            if not folder_dir.is_dir():
                continue
            folder_slug = folder_dir.name
            folder = folders_map.get(folder_slug)
            folder_name = folder.name if folder else folder_slug
            folder_id = folder.id if folder else None
            is_legacy = zone_slug == LEGACY_ZONE_SLUG

            for year_dir in sorted(folder_dir.iterdir()):
                if not year_dir.is_dir() or not _is_year_dir(year_dir.name):
                    continue
                y = int(year_dir.name)
                if year_filter is not None and y != year_filter:
                    continue
                prefix = f"{zone_slug}/{folder_slug}/{year_dir.name}"
                for item in _yield_files_in_year_dir(
                    root, year_dir, prefix, y, zone_id, zone_name, folder_id, folder_name, is_legacy
                ):
                    yield item


def safe_resolve(relative_path: str) -> Path:
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


def scan_images(
    db: Optional[Session] = None,
    year: Optional[int] = None,
    zone_id: Optional[int] = None,
    folder_id: Optional[int] = None,
    query: Optional[str] = None,
    legacy_only: Optional[bool] = None,
) -> List[LocalImageEntry]:
    ensure_root()
    lookup = build_slug_lookup(db) if db else SlugLookup()
    results: List[LocalImageEntry] = []
    seen_paths: set[str] = set()
    q = (query or "").strip().lower()

    for root in get_library_roots():
        for (r, y, path, rel, zid, zname, fid, fname, legacy) in _iter_all_images(root, lookup, year_filter=year):
            if rel in seen_paths:
                continue
            if zone_id is not None and zid != zone_id:
                continue
            if folder_id is not None and fid != folder_id:
                continue
            if legacy_only is True:
                parts = rel.split("/")
                if not (len(parts) == 2 and _is_year_dir(parts[0])):
                    continue
            if legacy_only is False and legacy and len(rel.split("/")) == 2:
                continue
            if q and q not in rel.lower() and q not in path.name.lower():
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
                    zone_id=zid,
                    zone_name=zname,
                    folder_id=fid,
                    folder_name=fname,
                    legacy=legacy,
                )
            )
    return results


def scan_years(db: Optional[Session] = None) -> List[dict]:
    """Flat year list (backward compat for rescan endpoint)."""
    counts: dict[int, int] = {}
    for entry in scan_images(db=db):
        counts[entry.year] = counts.get(entry.year, 0) + 1
    return [{"year": y, "imageCount": counts[y]} for y in sorted(counts)]


def scan_tree(db: Session) -> dict:
    """Nested zone → folder → year tree with image counts from disk."""
    lookup = build_slug_lookup(db)
    zone_nodes: dict[int, dict] = {}
    legacy_years: dict[int, int] = {}

    for entry in scan_images(db=db):
        if entry.legacy and not entry.zone_id:
            legacy_years[entry.year] = legacy_years.get(entry.year, 0) + 1
            continue
        if entry.zone_id is None:
            legacy_years[entry.year] = legacy_years.get(entry.year, 0) + 1
            continue

        zone = lookup.zone_by_id.get(entry.zone_id)
        if not zone:
            continue
        znode = zone_nodes.setdefault(entry.zone_id, {
            "id": zone.id,
            "name": zone.name,
            "slug": zone.slug,
            "folders": {},
        })
        fid = entry.folder_id or 0
        folder = lookup.folder_by_id.get(fid) if entry.folder_id else None
        fnode = znode["folders"].setdefault(fid, {
            "id": entry.folder_id,
            "name": entry.folder_name or (folder.name if folder else "Unknown"),
            "slug": folder.slug if folder else "",
            "years": {},
        })
        fnode["years"][entry.year] = fnode["years"].get(entry.year, 0) + 1

    # Include empty zones/folders from DB
    for zone in db.query(DdaZone).order_by(DdaZone.name).all():
        if not zone.slug:
            continue
        znode = zone_nodes.setdefault(zone.id, {
            "id": zone.id,
            "name": zone.name,
            "slug": zone.slug,
            "folders": {},
        })
        for folder in db.query(DdaVillage).filter(DdaVillage.zone_id == zone.id).order_by(DdaVillage.name).all():
            if not folder.slug:
                continue
            znode["folders"].setdefault(folder.id, {
                "id": folder.id,
                "name": folder.name,
                "slug": folder.slug,
                "years": {},
            })

    zones_out = []
    for znode in sorted(zone_nodes.values(), key=lambda z: z["name"].lower()):
        folders_out = []
        for fnode in sorted(znode["folders"].values(), key=lambda f: f["name"].lower()):
            years_out = [
                {"year": y, "imageCount": cnt}
                for y, cnt in sorted(fnode["years"].items())
            ]
            folders_out.append({
                "id": fnode["id"],
                "name": fnode["name"],
                "slug": fnode["slug"],
                "years": years_out,
            })
        zones_out.append({
            "id": znode["id"],
            "name": znode["name"],
            "slug": znode["slug"],
            "folders": folders_out,
        })

    return {
        "zones": zones_out,
        "legacyYears": [{"year": y, "imageCount": c} for y, c in sorted(legacy_years.items())],
    }


def entry_to_dict(entry: LocalImageEntry, include_meta: bool = False) -> dict:
    encoded_path = quote(entry.path, safe="/")
    breadcrumb = " / ".join(
        p for p in (entry.zone_name, entry.folder_name, str(entry.year), entry.filename) if p
    )
    out = {
        "path": entry.path,
        "year": entry.year,
        "filename": entry.filename,
        "fileSizeBytes": entry.file_size_bytes,
        "thumbUrl": f"/api/dda/local/thumb?path={encoded_path}",
        "source": "local_folder",
        "rootPath": str(entry.root),
        "zoneId": entry.zone_id,
        "zoneName": entry.zone_name,
        "folderId": entry.folder_id,
        "folderName": entry.folder_name,
        "legacy": entry.legacy,
        "breadcrumb": breadcrumb,
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
    try:
        if cache.exists() and cache.stat().st_mtime >= full.stat().st_mtime:
            return cache
        cache.parent.mkdir(parents=True, exist_ok=True)
        raster_to_preview_png(full, cache, max_side=max_side)
        return cache
    except Exception as exc:
        logger.warning("Thumb build failed for %s: %s", relative_path, exc)
        write_placeholder_png(cache, Path(relative_path).name, max_side)
        return cache


def ensure_root() -> None:
    for root in get_library_roots():
        root.mkdir(parents=True, exist_ok=True)
    LOCAL_THUMB_CACHE.mkdir(parents=True, exist_ok=True)


def count_files_in_folder(root: Path, zone_slug: str, folder_slug: str) -> int:
    folder_path = root / zone_slug / folder_slug
    if not folder_path.exists():
        return 0
    count = 0
    for path in folder_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            count += 1
    return count


def count_files_in_zone(root: Path, zone_slug: str) -> int:
    zone_path = root / zone_slug
    if not zone_path.exists():
        return 0
    count = 0
    for path in zone_path.rglob("*"):
        if path.is_file() and path.suffix.lower() in ALLOWED_EXTENSIONS:
            count += 1
    return count


def library_debug_info(db: Optional[Session] = None) -> dict:
    roots_info = []
    for root in get_library_roots():
        info = {"path": str(root), "exists": root.exists(), "zones": [], "legacyYears": []}
        if root.exists():
            for entry in sorted(root.iterdir()):
                if entry.is_dir() and _is_year_dir(entry.name):
                    files = [p.name for p in entry.iterdir() if p.is_file()]
                    info["legacyYears"].append({"year": entry.name, "files": files})
                elif entry.is_dir():
                    info["zones"].append(entry.name)
        roots_info.append(info)
    return {
        "roots": roots_info,
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "totalImages": len(scan_images(db=db)),
    }
