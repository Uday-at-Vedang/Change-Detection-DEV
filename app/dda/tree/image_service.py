"""Image upload and listing for tree nodes."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..config import ALLOWED_EXTENSIONS, LOCAL_THUMB_CACHE, max_upload_bytes_for_extension
from ..geotiff_io import bounds_to_json, inspect_image, raster_to_preview_png, write_placeholder_png
from ..upload_io import stream_upload_to_file
from .audit_service import log_action
from .models import ImageLibrary, TreeNode
from .path_service import images_dir, resolve_file, storage_root
from .query_compat import active_node_clause
from .tree_service import get_node_or_404

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"Satellite", "Drone", "Orthomosaic", "DEM", "GeoTIFF", "Raster", "PNG", "JPEG"}


def _safe_basename(filename: str) -> str:
    name = Path(filename or "upload").name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


def _thumb_cache_path(relative_path: str) -> Path:
    key = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:32]
    return LOCAL_THUMB_CACHE / f"{key}.png"


def _preview_cache_path(relative_path: str, max_side: int) -> Path:
    key = hashlib.sha256(f"{relative_path}:{max_side}".encode("utf-8")).hexdigest()[:32]
    return LOCAL_THUMB_CACHE / "preview" / f"{key}.png"


def _clear_image_caches(relative_path: str) -> None:
    _thumb_cache_path(relative_path).unlink(missing_ok=True)
    for max_side in (1024, 1600, 2048, 4096):
        _preview_cache_path(relative_path, max_side).unlink(missing_ok=True)


def _delete_related_sidecars(path: Path) -> None:
    """Remove common geospatial sidecars next to a library image."""
    candidates = [
        Path(f"{path}.aux.xml"),
        path.with_suffix(".tfw"),
        path.with_suffix(".tifw"),
        path.with_suffix(".jgw"),
        path.with_suffix(".jpgw"),
        path.with_suffix(".pgw"),
        path.with_suffix(".pngw"),
        path.with_suffix(".prj"),
        Path(f"{path}.wld"),
    ]
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        candidates.append(path.with_name(path.name + "w"))
    for sidecar in candidates:
        try:
            if sidecar.is_file():
                sidecar.unlink()
        except OSError as exc:
            logger.warning("Could not delete sidecar %s: %s", sidecar, exc)


def _as_iso(value) -> Optional[str]:
    """Serialize SQLite/MySQL datetimes the same way (always include a T)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if "T" not in text and len(text) >= 11 and text[10] == " ":
        return text.replace(" ", "T", 1)
    return text


def image_to_dict(img: ImageLibrary, node: Optional[TreeNode] = None) -> dict:
    rel = (img.file_path or "").replace("\\", "/")
    encoded = quote(rel, safe="/")
    bounds = None
    if img.bounds_json:
        try:
            bounds = json.loads(img.bounds_json)
        except json.JSONDecodeError:
            bounds = None
    return {
        "id": img.id,
        "nodeId": img.node_id,
        "path": rel,
        "filename": img.image_name,
        "imageName": img.image_name,
        "imageType": img.image_type,
        "nodePath": node.node_path if node else "",
        "breadcrumb": f"{node.node_path}/{img.image_name}" if node else img.image_name,
        "fileSizeBytes": img.file_size_bytes,
        "captureDate": _as_iso(img.capture_date),
        "uploadedBy": img.uploaded_by,
        "uploadedOn": _as_iso(img.uploaded_on),
        "thumbUrl": f"/api/dda/local/thumb?path={encoded}",
        "previewUrl": f"/api/dda/local/preview?path={encoded}",
        "mapInfoUrl": f"/api/dda/local/map-info?path={encoded}",
        "hasGeoref": bool(img.has_georef),
        "bounds": bounds,
        "width": img.width,
        "height": img.height,
        "format": img.format,
        "source": "tree_library",
    }


def _parse_manual_bounds(raw: Optional[str]) -> Optional[str]:
    """Parse a 'west,south,east,north' WGS84 string into bounds_json, or None."""
    if not raw or not raw.strip():
        return None
    try:
        parts = [float(x.strip()) for x in raw.replace("[", "").replace("]", "").split(",")]
    except ValueError:
        raise HTTPException(status_code=400, detail="manual_bounds must be 'west,south,east,north'")
    if len(parts) != 4:
        raise HTTPException(status_code=400, detail="manual_bounds must have 4 values: west,south,east,north")
    west, south, east, north = parts
    if abs(west) > 180 or abs(east) > 180 or abs(south) > 90 or abs(north) > 90:
        raise HTTPException(status_code=400, detail="manual_bounds out of range (lng ±180, lat ±90)")
    return bounds_to_json((west, south, east, north))


async def upload_image(
    db: Session,
    node_id: int,
    file: UploadFile,
    *,
    image_type: str,
    capture_date: Optional[str],
    uploaded_by: str,
    manual_bounds: Optional[str] = None,
) -> ImageLibrary:
    node = get_node_or_404(db, node_id)
    itype = image_type.strip() or "GeoTIFF"
    if itype not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"image_type must be one of: {', '.join(sorted(IMAGE_TYPES))}")

    original = _safe_basename(file.filename or "upload")
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    existing = (
        db.query(ImageLibrary)
        .filter(ImageLibrary.node_id == node.id, ImageLibrary.image_name == original)
        .first()
    )
    if existing is not None:
        logger.info("Skipped duplicate upload %s -> node %s (already exists as image id %s)",
                    original, node.id, existing.id)
        return existing

    dest_dir = images_dir(node.physical_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / original
    if dest.exists():
        stem, suffix = Path(original).stem, Path(original).suffix
        n = 1
        while dest.exists():
            dest = dest_dir / f"{stem}_{n}{suffix}"
            n += 1

    size = await stream_upload_to_file(file, dest, max_upload_bytes_for_extension(ext))
    rel = dest.relative_to(storage_root()).as_posix()

    cap = None
    if capture_date:
        try:
            cap = datetime.fromisoformat(capture_date.strip())
        except ValueError:
            try:
                cap = datetime.combine(date.fromisoformat(capture_date.strip()), datetime.min.time())
            except ValueError:
                raise HTTPException(status_code=400, detail="capture_date must be YYYY-MM-DD")

    meta = inspect_image(dest)
    # Prefer embedded/world-file georef; fall back to user-supplied manual bounds
    manual_json = _parse_manual_bounds(manual_bounds)
    bounds_json = bounds_to_json(meta.bounds_wgs84) or ""
    has_georef = meta.has_georef
    if not bounds_json and manual_json:
        bounds_json = manual_json
        has_georef = True
    img = ImageLibrary(
        node_id=node.id,
        image_name=dest.name,
        image_type=itype,
        file_path=rel,
        capture_date=cap,
        uploaded_by=uploaded_by or "",
        file_size_bytes=size,
        thumb_cache_key=hashlib.sha256(rel.encode()).hexdigest()[:32],
        width=meta.width,
        height=meta.height,
        has_georef=has_georef,
        bounds_json=bounds_json,
        format=meta.format,
    )
    db.add(img)
    log_action(db, "upload", node_id=node.id, new_value={"file": rel, "type": itype}, action_by=uploaded_by)
    db.commit()
    db.refresh(img)
    logger.info("Uploaded %s -> %s", original, rel)
    return img


def list_images_for_node(db: Session, node_id: int) -> List[dict]:
    node = get_node_or_404(db, node_id)
    rows = db.query(ImageLibrary).filter(ImageLibrary.node_id == node_id).order_by(ImageLibrary.uploaded_on.desc()).all()
    return [image_to_dict(r, node) for r in rows]


def get_image_by_file_path(db: Session, relative_path: str) -> Optional[ImageLibrary]:
    rel = (relative_path or "").replace("\\", "/").strip().lstrip("/")
    if not rel:
        return None
    row = db.query(ImageLibrary).filter(ImageLibrary.file_path == rel).first()
    if row:
        return row
    alt = rel.replace("/", "\\")
    if alt != rel:
        return db.query(ImageLibrary).filter(ImageLibrary.file_path == alt).first()
    return None


def _node_and_descendants_clause(node: TreeNode):
    """Images on this folder or any child folder (year / type subfolders)."""
    prefix = (node.node_path or "").rstrip("/")
    clauses = [ImageLibrary.node_id == node.id]
    if prefix:
        clauses.append(TreeNode.node_path == prefix)
        clauses.append(TreeNode.node_path.like(prefix + "/%"))
    return or_(*clauses)


def list_all_images(db: Session, *, node_id: Optional[int] = None, query: Optional[str] = None) -> List[dict]:
    q = (
        db.query(ImageLibrary, TreeNode)
        .outerjoin(TreeNode, ImageLibrary.node_id == TreeNode.id)
        .filter(or_(TreeNode.id.is_(None), active_node_clause(TreeNode.is_active)))
    )
    if node_id:
        node = db.query(TreeNode).filter(TreeNode.id == node_id).first()
        if node:
            q = q.filter(_node_and_descendants_clause(node))
        else:
            q = q.filter(ImageLibrary.node_id == node_id)
    if query:
        like = f"%{query.strip().lower()}%"
        q = q.filter(or_(
            func.lower(ImageLibrary.image_name).like(like),
            func.lower(func.coalesce(TreeNode.node_path, "")).like(like),
        ))
    rows = q.all()
    rows.sort(key=lambda pair: pair[0].uploaded_on or datetime.min, reverse=True)
    return [image_to_dict(img, node) for img, node in rows]


def get_or_build_thumb(relative_path: str, max_side: int = 256) -> Path:
    full = resolve_file(relative_path)
    cache = _thumb_cache_path(relative_path)
    try:
        if cache.exists() and cache.stat().st_mtime >= full.stat().st_mtime:
            return cache
        cache.parent.mkdir(parents=True, exist_ok=True)
        raster_to_preview_png(full, cache, max_side=max_side)
        return cache
    except Exception as exc:
        logger.warning("Thumb failed for %s: %s", relative_path, exc)
        write_placeholder_png(cache, Path(relative_path).name, max_side)
        return cache


def get_or_build_preview(relative_path: str, max_side: int = 1600) -> Path:
    """Larger cached preview for in-app image viewer."""
    full = resolve_file(relative_path)
    max_side = max(256, min(int(max_side), 4096))
    try:
        size = full.stat().st_size
        if size > 2 * 1024 ** 3:
            max_side = min(max_side, 512)
        elif size > 500 * 1024 ** 2:
            max_side = min(max_side, 1024)
    except OSError:
        pass
    cache = _preview_cache_path(relative_path, max_side)
    try:
        if cache.exists() and cache.stat().st_mtime >= full.stat().st_mtime:
            return cache
        cache.parent.mkdir(parents=True, exist_ok=True)
        raster_to_preview_png(full, cache, max_side=max_side)
        return cache
    except Exception as exc:
        logger.warning("Preview failed for %s: %s", relative_path, exc)
        write_placeholder_png(cache, Path(relative_path).name, min(max_side, 1024))
        return cache


def get_image_by_id(db: Session, image_id: int) -> Optional[ImageLibrary]:
    return db.query(ImageLibrary).filter(ImageLibrary.id == image_id).first()


def delete_library_image(
    db: Session,
    *,
    image_id: Optional[int] = None,
    relative_path: Optional[str] = None,
    action_by: str = "",
) -> dict:
    """Remove an image from disk and the library index."""
    img: Optional[ImageLibrary] = None
    if image_id is not None:
        img = get_image_by_id(db, image_id)
    elif relative_path:
        img = get_image_by_file_path(db, relative_path)
    if not img:
        raise HTTPException(status_code=404, detail="Image not found in library")

    rel = img.file_path
    node_id = img.node_id
    image_name = img.image_name
    saved_id = img.id

    try:
        full = resolve_file(rel)
        if full.is_file():
            full.unlink()
        _delete_related_sidecars(full)
    except FileNotFoundError:
        logger.warning("Library file already missing on disk: %s", rel)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not delete file: {exc}") from exc

    _clear_image_caches(rel)
    log_action(
        db,
        "delete_image",
        node_id=node_id,
        old_value={"file": rel, "name": image_name},
        action_by=action_by,
    )
    db.delete(img)
    db.commit()
    logger.info("Deleted library image %s (%s)", image_name, rel)
    return {"ok": True, "deletedPath": rel, "imageName": image_name, "imageId": saved_id}
