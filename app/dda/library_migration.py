"""One-time library layout migrations and slug backfill."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Set

from sqlalchemy.orm import Session

from .config import ALLOWED_EXTENSIONS, get_library_roots, get_writable_library_root
from .models import DdaLocalFileIndex, DdaVillage, DdaZone
from .path_slugs import slugify, unique_slug

logger = logging.getLogger(__name__)

LEGACY_ZONE_NAME = "Unassigned"
LEGACY_ZONE_SLUG = "_unassigned"
LEGACY_FOLDER_NAME = "Legacy"
LEGACY_FOLDER_SLUG = "legacy"


def _is_year_dir(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1990 <= int(name) <= 2100


def backfill_slugs(db: Session) -> dict:
    """Assign slugs to zones/folders missing them."""
    zone_slugs: Set[str] = set()
    for zone in db.query(DdaZone).filter(DdaZone.slug.isnot(None)).all():
        if zone.slug:
            zone_slugs.add(zone.slug)

    zones_updated = 0
    for zone in db.query(DdaZone).order_by(DdaZone.id).all():
        if zone.slug:
            continue
        zone.slug = unique_slug(zone.name, zone_slugs)
        zone_slugs.add(zone.slug)
        zones_updated += 1

    folders_updated = 0
    for zone in db.query(DdaZone).all():
        folder_slugs: Set[str] = set()
        for folder in db.query(DdaVillage).filter(
            DdaVillage.zone_id == zone.id, DdaVillage.slug.isnot(None)
        ).all():
            if folder.slug:
                folder_slugs.add(folder.slug)
        for folder in db.query(DdaVillage).filter(DdaVillage.zone_id == zone.id).order_by(DdaVillage.id).all():
            if folder.slug:
                continue
            folder.slug = unique_slug(folder.name, folder_slugs)
            folder_slugs.add(folder.slug)
            folders_updated += 1

    if zones_updated or folders_updated:
        db.commit()
    return {"zonesUpdated": zones_updated, "foldersUpdated": folders_updated}


def ensure_legacy_zone(db: Session) -> DdaZone:
    zone = db.query(DdaZone).filter(DdaZone.slug == LEGACY_ZONE_SLUG).first()
    if not zone:
        zone = db.query(DdaZone).filter(DdaZone.name == LEGACY_ZONE_NAME).first()
    if not zone:
        zone = DdaZone(name=LEGACY_ZONE_NAME, slug=LEGACY_ZONE_SLUG, mode="admin")
        db.add(zone)
        db.flush()
    elif not zone.slug:
        zone.slug = LEGACY_ZONE_SLUG
    folder = db.query(DdaVillage).filter(
        DdaVillage.zone_id == zone.id, DdaVillage.slug == LEGACY_FOLDER_SLUG
    ).first()
    if not folder:
        folder = db.query(DdaVillage).filter(
            DdaVillage.zone_id == zone.id, DdaVillage.name == LEGACY_FOLDER_NAME
        ).first()
    if not folder:
        folder = DdaVillage(zone_id=zone.id, name=LEGACY_FOLDER_NAME, slug=LEGACY_FOLDER_SLUG)
        db.add(folder)
        db.flush()
    elif not folder.slug:
        folder.slug = LEGACY_FOLDER_SLUG
    db.commit()
    return zone


def migrate_legacy_flat_years(db: Session) -> dict:
    """Move top-level library_sources/YEAR/* into _unassigned/legacy/YEAR/."""
    ensure_legacy_zone(db)
    moved = 0
    roots_touched = 0

    for root in get_library_roots():
        if not root.exists():
            continue
        root_changed = False
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or not _is_year_dir(entry.name):
                continue
            year = entry.name
            dest_dir = root / LEGACY_ZONE_SLUG / LEGACY_FOLDER_SLUG / year
            dest_dir.mkdir(parents=True, exist_ok=True)
            for path in sorted(entry.iterdir()):
                if not path.is_file():
                    continue
                if path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                target = dest_dir / path.name
                if target.exists():
                    stem, suffix = path.stem, path.suffix
                    n = 1
                    while target.exists():
                        target = dest_dir / f"{stem}_{n}{suffix}"
                        n += 1
                shutil.move(str(path), str(target))
                old_rel = f"{year}/{path.name}"
                new_rel = f"{LEGACY_ZONE_SLUG}/{LEGACY_FOLDER_SLUG}/{year}/{target.name}"
                idx = db.query(DdaLocalFileIndex).filter(DdaLocalFileIndex.relative_path == old_rel).first()
                if idx:
                    idx.relative_path = new_rel
                moved += 1
                root_changed = True
            try:
                if entry.is_dir() and not any(entry.iterdir()):
                    entry.rmdir()
            except OSError:
                pass
        if root_changed:
            roots_touched += 1

    if moved:
        db.commit()
        logger.info("Legacy library migration: moved %d file(s) across %d root(s)", moved, roots_touched)
    return {"moved": moved, "rootsTouched": roots_touched}


def ensure_zone_folder_dirs(db: Session) -> None:
    """Create on-disk folders for all zones/folders."""
    root = get_writable_library_root()
    root.mkdir(parents=True, exist_ok=True)
    for zone in db.query(DdaZone).filter(DdaZone.slug.isnot(None)).all():
        zone_dir = root / zone.slug
        zone_dir.mkdir(parents=True, exist_ok=True)
        for folder in db.query(DdaVillage).filter(DdaVillage.zone_id == zone.id).all():
            if folder.slug:
                (zone_dir / folder.slug).mkdir(parents=True, exist_ok=True)
