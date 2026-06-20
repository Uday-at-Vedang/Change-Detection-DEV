"""Slim local helpers: thumb, resolve, detect — backed by tree library."""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse
from PIL import Image
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .dda_auth import current_dda_user
from .detect_service import run_detection_and_save
from .geotiff_io import load_rgb_pil

from .config import (
    IS_DDA_MODE,
    MAX_GEOTIFF_BYTES,
    geotiff_io_available,
    get_detection_max_side,
    get_library_roots,
    get_storage_root,
    is_hf_hosted,
)
from .tree.image_service import get_or_build_thumb, list_all_images
from .tree.path_service import resolve_file
from .tree.tree_service import build_tree

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(
            status_code=404,
            detail="DDA mode is not enabled. On HF dev Space this is automatic; locally use: python run.py",
        )


def safe_resolve(relative_path: str) -> Path:
    try:
        return resolve_file(relative_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/local/config")
def local_library_config():
    _require_dda()
    storage = str(get_storage_root())
    hosted = is_hf_hosted()
    instructions = (
        "Select a node in the tree, choose image type, and upload. "
        "Files are stored under {zone}/{area}/…/Images/ in persistent storage."
        if hosted
        else "Use the tree library to organize images by zone/area/year, or upload via the form."
    )
    return {
        "source": "tree_library",
        "isHosted": hosted,
        "spaceId": __import__("os").environ.get("SPACE_ID", ""),
        "appMode": "dda" if IS_DDA_MODE else "legacy",
        "rootPath": storage,
        "rootPaths": [str(r) for r in get_library_roots()],
        "writablePath": storage,
        "storageRoot": storage,
        "instructions": instructions,
        "geotiffEnabled": geotiff_io_available(),
        "detectionMaxSide": get_detection_max_side(),
        "maxGeotiffMb": MAX_GEOTIFF_BYTES // (1024 * 1024),
        "maxGeotiffBytes": MAX_GEOTIFF_BYTES,
        "maxUploadGb": round(MAX_GEOTIFF_BYTES / (1024 ** 3), 2),
    }


@router.get("/local/images")
def local_images(
    node_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_dda()
    return list_all_images(db, node_id=node_id, query=q)


@router.get("/local/thumb")
def local_thumb(path: str = Query(...)):
    _require_dda()
    try:
        thumb = get_or_build_thumb(path)
        return FileResponse(thumb, media_type="image/png")
    except Exception as exc:
        logger.warning("Thumb endpoint fallback for %s: %s", path, exc)
        from .config import LOCAL_THUMB_CACHE
        from .geotiff_io import write_placeholder_png

        cache = LOCAL_THUMB_CACHE / "fallback.png"
        write_placeholder_png(cache, Path(path).name)
        return FileResponse(cache, media_type="image/png")


@router.post("/local/rescan")
def local_rescan(db: Session = Depends(get_db)):
    _require_dda()
    tree = build_tree(db)
    images = list_all_images(db)
    return {
        "ok": True,
        "tree": tree,
        "totalImages": len(images),
        "storageRoot": str(get_storage_root()),
    }


@router.post("/detect/from-library")
async def detect_from_library(
    request: Request,
    base_path: str = Form(...),
    comparison_path: str = Form(...),
    method: str = Form("AI-Based Deep Learning"),
    title: str = Form("Untitled run"),
    zone: str = Form(""),
    village: str = Form(""),
    enable_registration: bool = Form(True),
    enable_normalization: bool = Form(True),
    detection_sensitivity: float = Form(0.5),
    min_region_area: Optional[int] = Form(150),
    notify_email: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Run change detection on two library images by relative path."""
    _require_dda()
    base_norm = base_path.replace("\\", "/").strip()
    comp_norm = comparison_path.replace("\\", "/").strip()
    if not base_norm or not comp_norm:
        raise HTTPException(status_code=400, detail="base_path and comparison_path are required")
    if base_norm == comp_norm:
        raise HTTPException(status_code=400, detail="Base and comparison images must be different")

    try:
        base_file = safe_resolve(base_norm)
        comp_file = safe_resolve(comp_norm)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid library path: {exc}") from exc

    try:
        before_pil = load_rgb_pil(base_file, max_side=get_detection_max_side())
        after_pil = load_rgb_pil(comp_file, max_side=get_detection_max_side())
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not load images: {exc}") from exc

    if before_pil.size != after_pil.size:
        after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)

    if title == "Untitled run":
        title = f"{Path(base_norm).name} vs {Path(comp_norm).name}"

    try:
        return run_detection_and_save(
            db,
            before_pil,
            after_pil,
            method=method,
            title=title,
            zone=zone,
            village=village,
            enable_registration=enable_registration,
            enable_normalization=enable_normalization,
            detection_sensitivity=detection_sensitivity,
            min_region_area=min_region_area,
            notify_email=notify_email,
            max_size=get_detection_max_side(),
            geo_bounds_path=base_file,
            user_id=user.id,
        )
    except Exception as exc:
        logger.exception("Library detection failed for %s vs %s", base_norm, comp_norm)
        raise HTTPException(status_code=500, detail=f"Detection failed: {exc}") from exc
