"""Filesystem → DB sync for tree library folders and images."""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Optional

from sqlalchemy.orm import Session

from ..config import ALLOWED_EXTENSIONS
from ..geotiff_io import bounds_to_json, inspect_image
from .audit_service import log_action
from .models import ImageLibrary, TreeNode
from .path_service import ensure_node_directory, storage_root
from .path_slugs import RESERVED

from .path_slugs import RESERVED

logger = logging.getLogger(__name__)

_NODE_TYPES = ("Zone", "Area", "Year", "Folder")
_SKIP_DIRS = frozenset({".git", ".thumbs", "__pycache__", "cache", "thumbs"})
_RESERVED_DIR_NAMES = RESERVED


def _infer_node_type(depth: int) -> str:
    return _NODE_TYPES[min(depth, len(_NODE_TYPES) - 1)]


def _display_name(folder_slug: str) -> str:
    name = folder_slug.replace("_", " ").replace("-", " ").strip()
    return name or folder_slug


def _find_node_by_physical_path(db: Session, physical_path: str) -> Optional[TreeNode]:
    rel = (physical_path or "").strip("/")
    if not rel:
        return None
    return (
        db.query(TreeNode)
        .filter(TreeNode.physical_path == rel, TreeNode.is_active == True)  # noqa: E712
        .first()
    )


def ensure_node_from_disk(db: Session, physical_path: str, *, created_by: str = "filesystem-sync") -> TreeNode:
    """Ensure a TreeNode exists for a disk folder path (slug segments)."""
    rel = (physical_path or "").strip("/")
    if not rel:
        raise ValueError("physical_path is required")

    existing = _find_node_by_physical_path(db, rel)
    if existing:
        ensure_node_directory(rel)
        return existing

    parts = rel.split("/")
    parent_id = None
    parent_display = ""
    if len(parts) > 1:
        parent = ensure_node_from_disk(db, "/".join(parts[:-1]), created_by=created_by)
        parent_id = parent.id
        parent_display = parent.node_path or parent.node_name

    folder_slug = parts[-1]
    if folder_slug.lower() in RESERVED or folder_slug.lower() == "images":
        raise ValueError(f"Reserved folder name: {folder_slug}")

    sibling = (
        db.query(TreeNode)
        .filter(
            TreeNode.parent_id == parent_id,
            TreeNode.slug == folder_slug,
            TreeNode.is_active == True,  # noqa: E712
        )
        .first()
    )
    if sibling:
        ensure_node_directory(sibling.physical_path)
        return sibling

    node_name = _display_name(folder_slug)
    node_path = f"{parent_display}/{node_name}".strip("/") if parent_display else node_name
    node = TreeNode(
        parent_id=parent_id,
        node_name=node_name,
        node_type=_infer_node_type(len(parts) - 1),
        node_level=max(0, len(parts) - 1),
        node_path=node_path,
        slug=folder_slug,
        physical_path=rel,
        created_by=created_by,
    )
    db.add(node)
    db.flush()
    ensure_node_directory(rel)
    log_action(
        db,
        "sync_create",
        node_id=node.id,
        new_value={"name": node_name, "path": node_path, "physical": rel},
        action_by=created_by,
    )
    db.commit()
    db.refresh(node)
    logger.info("Synced tree node from disk: %s", rel)
    return node


def _image_type_for(file_path: Path) -> str:
    ext = file_path.suffix.lower()
    if ext in (".tif", ".tiff"):
        return "GeoTIFF"
    if ext in (".jpg", ".jpeg"):
        return "JPEG"
    if ext == ".png":
        return "PNG"
    return "Raster"


def _index_image_file(
    db: Session, node: TreeNode, file_path: Path, rel_file: str, stats: dict,
) -> bool:
    """Index one library file. Returns True if newly created.

    Unreadable rasters (corrupt TIFF, etc.) are skipped and counted in
    ``stats['imagesSkipped']`` so one bad file cannot fail the whole rescan.
    """
    existing = db.query(ImageLibrary).filter(ImageLibrary.file_path == rel_file).first()
    stat = file_path.stat()
    try:
        meta = inspect_image(file_path)
    except Exception as exc:
        logger.warning("Skipping unreadable image %s: %s", rel_file, exc)
        stats["imagesSkipped"] = stats.get("imagesSkipped", 0) + 1
        return False
    if meta.width <= 0 or meta.height <= 0:
        logger.warning(
            "Skipping image with invalid dimensions (%sx%s): %s",
            meta.width, meta.height, rel_file,
        )
        stats["imagesSkipped"] = stats.get("imagesSkipped", 0) + 1
        return False

    if existing:
        changed = (
            existing.file_size_bytes != stat.st_size
            or existing.node_id != node.id
            or existing.width != meta.width
            or existing.height != meta.height
        )
        if changed:
            existing.node_id = node.id
            existing.file_size_bytes = stat.st_size
            existing.width = meta.width
            existing.height = meta.height
            existing.has_georef = meta.has_georef
            existing.bounds_json = bounds_to_json(meta.bounds_wgs84) or existing.bounds_json
            existing.format = meta.format
            db.commit()
            stats["imagesUpdated"] += 1
        return False

    img = ImageLibrary(
        node_id=node.id,
        image_name=file_path.name,
        image_type=_image_type_for(file_path),
        file_path=rel_file,
        uploaded_by="filesystem-sync",
        file_size_bytes=stat.st_size,
        thumb_cache_key=hashlib.sha256(rel_file.encode()).hexdigest()[:32],
        width=meta.width,
        height=meta.height,
        has_georef=meta.has_georef,
        bounds_json=bounds_to_json(meta.bounds_wgs84) or "",
        format=meta.format,
    )
    db.add(img)
    db.commit()
    logger.info("Indexed image from disk: %s", rel_file)
    return True


def _node_path_for_images_folder(rel_path: str) -> Optional[str]:
    """Resolve which tree node should own an on-disk Images/ folder."""
    rel = (rel_path or "").strip("/")
    while rel:
        if rel.split("/")[-1].lower() not in _RESERVED_DIR_NAMES:
            return rel
        rel = "/".join(rel.split("/")[:-1])
    return None


def _sync_directory(db: Session, abs_dir: Path, rel_path: str, stats: dict) -> None:
    """Recursively sync nodes and images under rel_path."""
    if not abs_dir.is_dir():
        return

    for child in sorted(abs_dir.iterdir()):
        if not child.is_dir() or child.name.startswith(".") or child.name in _SKIP_DIRS:
            continue

        child_rel = f"{rel_path}/{child.name}".strip("/") if rel_path else child.name

        if child.name.lower() == "images":
            node_rel = _node_path_for_images_folder(rel_path)
            if node_rel:
                node = _find_node_by_physical_path(db, node_rel)
                if not node:
                    try:
                        node = ensure_node_from_disk(db, node_rel)
                    except ValueError as exc:
                        logger.warning("Skipping images under %s: %s", rel_path, exc)
                        stats["foldersSkipped"] = stats.get("foldersSkipped", 0) + 1
                        continue
                for f in sorted(child.iterdir()):
                    if not f.is_file() or f.suffix.lower() not in ALLOWED_EXTENSIONS:
                        continue
                    rel_file = f.relative_to(storage_root()).as_posix()
                    if _index_image_file(db, node, f, rel_file, stats):
                        stats["imagesIndexed"] += 1
            continue

        if child.name.lower() in _RESERVED_DIR_NAMES:
            logger.warning("Skipping reserved folder on disk: %s", child_rel)
            stats["foldersSkipped"] = stats.get("foldersSkipped", 0) + 1
            _sync_directory(db, child, child_rel, stats)
            continue

        before = _find_node_by_physical_path(db, child_rel)
        try:
            ensure_node_from_disk(db, child_rel)
        except ValueError as exc:
            logger.warning("Skipping folder %s: %s", child_rel, exc)
            stats["foldersSkipped"] = stats.get("foldersSkipped", 0) + 1
            _sync_directory(db, child, child_rel, stats)
            continue
        if not before:
            stats["nodesCreated"] += 1
        _sync_directory(db, child, child_rel, stats)


def sync_from_filesystem(db: Session) -> dict:
    """Import disk folders/images into tree_nodes and image_library."""
    root = storage_root()
    stats = {
        "nodesCreated": 0,
        "imagesIndexed": 0,
        "imagesUpdated": 0,
        "imagesSkipped": 0,
        "orphansFlagged": 0,
        "foldersSkipped": 0,
    }
    if not root.exists():
        return stats

    _sync_directory(db, root, "", stats)

    # Flag DB images whose files are missing on disk
    for img in db.query(ImageLibrary).all():
        full = storage_root() / img.file_path.replace("\\", "/")
        if not full.exists():
            stats["orphansFlagged"] += 1

    logger.info("Filesystem sync complete: %s", stats)
    return stats
