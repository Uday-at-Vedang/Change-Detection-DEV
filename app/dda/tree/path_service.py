"""Physical directory sync for tree nodes."""
from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..config import get_storage_root

logger = logging.getLogger(__name__)


def storage_root() -> Path:
    root = get_storage_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def absolute_path(relative_physical_path: str) -> Path:
    rel = (relative_physical_path or "").replace("\\", "/").strip("/")
    return storage_root() / rel if rel else storage_root()


def images_dir(relative_physical_path: str) -> Path:
    return absolute_path(relative_physical_path) / "Images"


def ensure_node_directory(relative_physical_path: str) -> Path:
    path = absolute_path(relative_physical_path)
    path.mkdir(parents=True, exist_ok=True)
    (path / "Images").mkdir(parents=True, exist_ok=True)
    return path


def move_directory(old_rel: str, new_rel: str) -> None:
    old_abs = absolute_path(old_rel)
    new_abs = absolute_path(new_rel)
    if not old_abs.exists():
        ensure_node_directory(new_rel)
        return
    new_abs.parent.mkdir(parents=True, exist_ok=True)
    if new_abs.exists():
        raise OSError(f"Destination already exists: {new_rel}")
    shutil.move(str(old_abs), str(new_abs))
    logger.info("Moved tree folder %s -> %s", old_rel, new_rel)


def delete_directory(relative_physical_path: str) -> None:
    path = absolute_path(relative_physical_path)
    if path.exists() and path.is_dir():
        shutil.rmtree(path)
        logger.info("Deleted tree folder %s", relative_physical_path)


def resolve_file(relative_file_path: str) -> Path:
    rel = relative_file_path.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("Invalid file path")
    full = (storage_root() / rel).resolve()
    try:
        full.relative_to(storage_root().resolve())
    except ValueError as exc:
        raise ValueError("Path escapes storage root") from exc
    if not full.is_file():
        raise FileNotFoundError(relative_file_path)
    return full
