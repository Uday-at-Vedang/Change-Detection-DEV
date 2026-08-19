"""Slim local helpers: thumb, resolve, detect — backed by tree library."""
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from PIL import Image
from pydantic import BaseModel
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .dda_auth import current_dda_user, require_min_role
from .detect_service import run_detection_and_save
from .geotiff_io import load_rgb_pil

from .config import (
    IS_DDA_MODE,
    MAX_GEOTIFF_BYTES,
    MAX_IMAGE_BYTES,
    geotiff_io_available,
    get_detection_max_side,
    get_library_roots,
    get_storage_root,
    is_hf_hosted,
)
from .tree.image_service import (
    delete_library_image,
    get_or_build_preview,
    get_or_build_thumb,
    list_all_images,
)
from .tree.path_service import resolve_file
from .tree.tree_service import build_tree

logger = logging.getLogger(__name__)
router = APIRouter()


class LocalImageDeleteBody(BaseModel):
    path: str


def _normalize_library_path(path: str) -> str:
    return unquote((path or "").replace("\\", "/").strip().lstrip("/"))


def _delete_image_by_path(db: Session, user: User, rel: str) -> dict:
    require_min_role(user, db, "viewer")
    if not rel:
        raise HTTPException(status_code=400, detail="path is required")
    result = delete_library_image(
        db, relative_path=rel, action_by=user.email or str(user.id),
    )
    return {"ok": True, **result}


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
def local_library_config(user: User = Depends(current_dda_user)):
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
        "maxImageMb": MAX_IMAGE_BYTES // (1024 * 1024),
        "maxImageBytes": MAX_IMAGE_BYTES,
        "maxUploadGb": round(MAX_GEOTIFF_BYTES / (1024 ** 3), 2),
    }


@router.get("/local/images")
def local_images(
    node_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    return list_all_images(db, node_id=node_id, query=q)


@router.get("/local/thumb")
def local_thumb(path: str = Query(...), user: User = Depends(current_dda_user)):
    _require_dda()
    path = _normalize_library_path(path)
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


@router.get("/local/preview")
def local_preview(
    path: str = Query(...),
    max: int = Query(1600, ge=256, le=4096),
    user: User = Depends(current_dda_user),
):
    """Full-size preview for library image viewer (cached PNG)."""
    _require_dda()
    path = _normalize_library_path(path)
    try:
        preview = get_or_build_preview(path, max_side=max)
        return FileResponse(preview, media_type="image/png")
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("Preview endpoint failed for %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"Could not build preview: {exc}") from exc


@router.get("/local/map-basemaps")
def local_map_basemaps(user: User = Depends(current_dda_user)):
    """XYZ basemap presets (OSM, Google Satellite, custom URL)."""
    _require_dda()
    from .map_tiles import BASEMAP_PRESETS
    return {"basemaps": BASEMAP_PRESETS}


@router.get("/local/map-info")
def local_map_info(
    path: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """WGS84 bounds + tile URL for overlaying a library raster on a web map."""
    _require_dda()
    path = _normalize_library_path(path)
    try:
        from .map_tiles import build_map_info
        return build_map_info(path, db=db)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("map-info failed for %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"Could not read georef: {exc}") from exc


@router.get("/local/map-tiles/{z}/{x}/{y}.png")
def local_map_tile(
    z: int,
    x: int,
    y: int,
    path: str = Query(...),
    user: User = Depends(current_dda_user),
):
    """XYZ tile of a georeferenced GeoTIFF warped to Web Mercator."""
    _require_dda()
    path = _normalize_library_path(path)
    try:
        from .map_tiles import render_xyz_tile
        png = render_xyz_tile(path, z, x, y)
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/local/map-overview")
def local_map_overview(
    path: str = Query(...),
    max: int = Query(2048, ge=256, le=4096),
    user: User = Depends(current_dda_user),
):
    """Web-Mercator overview PNG for distortion-free overlay on XYZ basemaps."""
    _require_dda()
    path = _normalize_library_path(path)
    try:
        from .map_tiles import render_mercator_overview
        png = render_mercator_overview(path, max_side=max)
        return Response(
            content=png,
            media_type="image/png",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Image file not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.warning("map-overview failed for %s: %s", path, exc)
        raise HTTPException(status_code=500, detail=f"Could not build overlay: {exc}") from exc


@router.post("/local/images/delete")
def local_delete_image_post(
    body: LocalImageDeleteBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Delete a library image (preferred — works reliably across browsers)."""
    _require_dda()
    return _delete_image_by_path(db, user, _normalize_library_path(body.path))


@router.delete("/local/images")
def local_delete_image(
    body: Optional[LocalImageDeleteBody] = None,
    path: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rel = _normalize_library_path(body.path if body and body.path else (path or ""))
    return _delete_image_by_path(db, user, rel)


@router.post("/local/rescan")
def local_rescan(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    from .tree.sync_service import sync_from_filesystem

    try:
        sync_stats = sync_from_filesystem(db)
    except Exception as exc:
        logger.exception("Filesystem rescan failed")
        raise HTTPException(status_code=500, detail=f"Rescan failed: {exc}") from exc
    tree = build_tree(db)
    images = list_all_images(db)
    return {
        "ok": True,
        "tree": tree,
        "totalImages": len(images),
        "storageRoot": str(get_storage_root()),
        "sync": sync_stats,
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
    roi: Optional[str] = Form(None),
    shape_mode: str = Form("polygon"),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Run change detection on two library images by relative path.

    ``roi`` (optional) is a JSON ``{"x","y","w","h"}`` fractional window in
    [0,1]; when present, only that cropped region is detected.
    """
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

    roi_dict = None
    if roi and roi.strip():
        import json as _json

        from .geotiff_io import parse_roi
        try:
            roi_dict = parse_roi(_json.loads(roi))
        except (ValueError, _json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=f"Invalid roi: {exc}") from exc

    try:
        from ..detection_config import get_load_max_side
        from .geotiff_io import load_rgb_roi
        load_side = get_load_max_side()
        if roi_dict:
            before_pil = load_rgb_roi(base_file, roi_dict, max_side=load_side)
            after_pil = load_rgb_roi(comp_file, roi_dict, max_side=load_side)
        else:
            before_pil = load_rgb_pil(base_file, max_side=load_side)
            after_pil = load_rgb_pil(comp_file, max_side=load_side)
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not load images: {exc}") from exc

    gsd_debug = None
    try:
        from .geotiff_io import harmonize_pair_gsd
        before_pil, after_pil, gsd_debug = harmonize_pair_gsd(
            before_pil, after_pil, base_file, comp_file)
    except Exception as exc:
        logger.warning("GSD harmonization skipped: %s", exc)

    if before_pil.size != after_pil.size:
        after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)

    if title == "Untitled run":
        title = f"{Path(base_norm).name} vs {Path(comp_norm).name}"

    try:
        # Use fullres load cap (up to DETECTION_FULLRES_MAX_SIDE), not the
        # legacy 4096 downscaled cap — otherwise max-res config is ignored.
        detect_max = load_side
        logger.info(
            "Library detection max_size=%d (loaded %s)",
            detect_max, before_pil.size,
        )
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
            max_size=detect_max,
            geo_bounds_path=base_file,
            comparison_file=comp_file,
            base_path=base_norm,
            comparison_path=comp_norm,
            user_id=user.id,
            gsd_debug=gsd_debug,
            shape_mode=shape_mode,
        )
    except Exception as exc:
        logger.exception("Library detection failed for %s vs %s", base_norm, comp_norm)
        raise HTTPException(status_code=500, detail=f"Detection failed: {exc}") from exc
