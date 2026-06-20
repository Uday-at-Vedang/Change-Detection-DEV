"""Image upload and listing for tree nodes."""
from __future__ import annotations

import hashlib
import logging
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import ALLOWED_EXTENSIONS, LOCAL_THUMB_CACHE, max_upload_bytes_for_extension
from ..geotiff_io import bounds_to_json, inspect_image, raster_to_preview_png, write_placeholder_png
from ..upload_io import stream_upload_to_file
from .audit_service import log_action
from .models import ImageLibrary, TreeNode
from .path_service import images_dir, resolve_file, storage_root
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


def image_to_dict(img: ImageLibrary, node: Optional[TreeNode] = None) -> dict:
    encoded = quote(img.file_path, safe="/")
    return {
        "id": img.id,
        "nodeId": img.node_id,
        "path": img.file_path,
        "filename": img.image_name,
        "imageName": img.image_name,
        "imageType": img.image_type,
        "nodePath": node.node_path if node else "",
        "breadcrumb": f"{node.node_path}/{img.image_name}" if node else img.image_name,
        "fileSizeBytes": img.file_size_bytes,
        "captureDate": img.capture_date.isoformat() if img.capture_date else None,
        "uploadedBy": img.uploaded_by,
        "uploadedOn": img.uploaded_on.isoformat() if img.uploaded_on else None,
        "thumbUrl": f"/api/dda/local/thumb?path={encoded}",
        "hasGeoref": img.has_georef,
        "width": img.width,
        "height": img.height,
        "format": img.format,
        "source": "tree_library",
    }


async def upload_image(
    db: Session,
    node_id: int,
    file: UploadFile,
    *,
    image_type: str,
    capture_date: Optional[str],
    uploaded_by: str,
) -> ImageLibrary:
    node = get_node_or_404(db, node_id)
    itype = image_type.strip() or "GeoTIFF"
    if itype not in IMAGE_TYPES:
        raise HTTPException(status_code=400, detail=f"image_type must be one of: {', '.join(sorted(IMAGE_TYPES))}")

    original = _safe_basename(file.filename or "upload")
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Allowed extensions: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

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
        has_georef=meta.has_georef,
        bounds_json=bounds_to_json(meta.bounds_wgs84) or "",
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


def list_all_images(db: Session, *, node_id: Optional[int] = None, query: Optional[str] = None) -> List[dict]:
    q = db.query(ImageLibrary, TreeNode).join(TreeNode, ImageLibrary.node_id == TreeNode.id).filter(TreeNode.is_active == True)  # noqa: E712
    if node_id:
        q = q.filter(ImageLibrary.node_id == node_id)
    if query:
        like = f"%{query.strip().lower()}%"
        q = q.filter(
            (ImageLibrary.image_name.ilike(like)) | (TreeNode.node_path.ilike(like))
        )
    q = q.order_by(ImageLibrary.uploaded_on.desc())
    return [image_to_dict(img, node) for img, node in q.all()]


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
