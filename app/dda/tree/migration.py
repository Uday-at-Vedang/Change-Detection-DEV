"""One-time migration: Delhi seed + flat year folders -> tree."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import ALLOWED_EXTENSIONS, get_library_roots, get_storage_root
from ..seed_data import DELHI_ZONES
from .models import ImageLibrary, TreeNode
from .path_service import ensure_node_directory, storage_root
from .path_slugs import unique_slug
from .tree_service import create_node

logger = logging.getLogger(__name__)

LEGACY_ZONE = "Unassigned"
LEGACY_AREA = "Legacy"


def _is_year_dir(name: str) -> bool:
    return len(name) == 4 and name.isdigit() and 1990 <= int(name) <= 2100


def seed_delhi_tree(db: Session) -> dict:
    if db.query(TreeNode).count() > 0:
        return {"seeded": False, "nodes": db.query(TreeNode).count()}

    zones_created = 0
    areas_created = 0
    for zone_name, areas in DELHI_ZONES.items():
        zone = create_node(db, parent_id=None, node_name=zone_name, node_type="Zone", created_by="system")
        zones_created += 1
        for area_name in areas:
            create_node(db, parent_id=zone.id, node_name=area_name, node_type="Area", created_by="system")
            areas_created += 1

    return {"seeded": True, "zones": zones_created, "areas": areas_created}


def _find_or_create_legacy_branch(db: Session) -> TreeNode:
    zone = db.query(TreeNode).filter(TreeNode.parent_id == None, TreeNode.node_name == LEGACY_ZONE).first()  # noqa: E711
    if not zone:
        zone = create_node(db, parent_id=None, node_name=LEGACY_ZONE, node_type="Zone", created_by="system")
    area = db.query(TreeNode).filter(TreeNode.parent_id == zone.id, TreeNode.node_name == LEGACY_AREA).first()
    if not area:
        area = create_node(db, parent_id=zone.id, node_name=LEGACY_AREA, node_type="Area", created_by="system")
    return area


def _find_or_create_year_node(db: Session, parent: TreeNode, year: str) -> TreeNode:
    existing = db.query(TreeNode).filter(TreeNode.parent_id == parent.id, TreeNode.node_name == year).first()
    if existing:
        return existing
    return create_node(db, parent_id=parent.id, node_name=year, node_type="Year", created_by="system")


def migrate_flat_year_folders(db: Session) -> dict:
    moved = 0
    indexed = 0
    legacy_area = _find_or_create_legacy_branch(db)
    root = get_storage_root()

    for lib_root in get_library_roots():
        if not lib_root.exists() or lib_root.resolve() != root.resolve():
            continue
        for entry in sorted(lib_root.iterdir()):
            if not entry.is_dir() or not _is_year_dir(entry.name):
                continue
            year_node = _find_or_create_year_node(db, legacy_area, entry.name)
            dest_images = ensure_node_directory(year_node.physical_path) / "Images"
            for path in sorted(entry.iterdir()):
                if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTENSIONS:
                    continue
                target = dest_images / path.name
                if target.exists():
                    stem, suffix = path.stem, path.suffix
                    n = 1
                    while target.exists():
                        target = dest_images / f"{stem}_{n}{suffix}"
                        n += 1
                if path.resolve() != target.resolve():
                    shutil.move(str(path), str(target))
                rel = target.relative_to(root).as_posix()
                if not db.query(ImageLibrary).filter(ImageLibrary.file_path == rel).first():
                    db.add(ImageLibrary(
                        node_id=year_node.id,
                        image_name=target.name,
                        image_type="GeoTIFF",
                        file_path=rel,
                        uploaded_by="migration",
                        file_size_bytes=target.stat().st_size,
                    ))
                    indexed += 1
                moved += 1
            try:
                if entry.is_dir() and not any(entry.iterdir()):
                    entry.rmdir()
            except OSError:
                pass

    if moved or indexed:
        db.commit()
        logger.info("Tree migration: moved=%d indexed=%d", moved, indexed)
    return {"moved": moved, "indexed": indexed}


def run_tree_migration(db: Session) -> dict:
    seed_result = seed_delhi_tree(db)
    migrate_result = migrate_flat_year_folders(db)
    return {"seed": seed_result, "migrate": migrate_result}
