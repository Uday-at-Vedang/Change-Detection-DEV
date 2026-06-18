"""API for reading images from local library_sources/ zone/folder/year hierarchy."""
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .dda_auth import current_dda_user, require_min_role
from .detect_service import run_detection_and_save
from .geotiff_io import load_rgb_pil

from .config import (
    IS_DDA_MODE,
    MAX_GEOTIFF_BYTES,
    geotiff_io_available,
    get_detection_max_side,
    get_library_roots,
    get_writable_library_root,
    is_hf_hosted,
    max_upload_bytes_for_extension,
)
from .local_library import (
    build_upload_dest,
    entry_to_dict,
    get_or_build_thumb,
    library_debug_info,
    safe_resolve,
    scan_images,
    scan_tree,
    scan_years,
)
from .models import DdaLocalFileIndex, DdaVillage, DdaZone
from .upload_io import stream_upload_to_file

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(
            status_code=404,
            detail="DDA mode is not enabled. On HF dev Space this is automatic; locally use: python run.py",
        )


def _safe_basename(filename: str) -> str:
    name = Path(filename or "upload").name
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


def _upsert_file_index(db: Session, rel_path: str, zone_id: int, folder_id: int, year: int, user_id: int):
    idx = db.query(DdaLocalFileIndex).filter(DdaLocalFileIndex.relative_path == rel_path).first()
    if idx:
        idx.zone_id = zone_id
        idx.folder_id = folder_id
        idx.year = year
        idx.uploaded_by = user_id
    else:
        db.add(DdaLocalFileIndex(
            relative_path=rel_path,
            zone_id=zone_id,
            folder_id=folder_id,
            year=year,
            uploaded_by=user_id,
        ))
    db.commit()


class ReassignBody(BaseModel):
    path: str
    zone_id: int
    folder_id: int
    year: int = Field(..., ge=1990, le=2100)


@router.get("/local/config")
def local_library_config():
    _require_dda()
    roots = [str(r) for r in get_library_roots()]
    writable = str(get_writable_library_root())
    hosted = is_hf_hosted()
    if hosted:
        instructions = (
            "Upload images below with zone, folder, and year. Files are saved under "
            "library_sources/{zone}/{folder}/{year}/ on persistent Space storage."
        )
    else:
        instructions = (
            "Copy .tif images into library_sources/{zone}/{folder}/{year}/, or use Upload "
            "with zone and folder selected. Click Refresh after adding files manually."
        )
    return {
        "source": "local_folder",
        "isHosted": hosted,
        "spaceId": __import__("os").environ.get("SPACE_ID", ""),
        "appMode": "dda" if IS_DDA_MODE else "legacy",
        "rootPath": roots[0] if roots else "",
        "rootPaths": roots,
        "writablePath": writable,
        "instructions": instructions,
        "geotiffEnabled": geotiff_io_available(),
        "detectionMaxSide": get_detection_max_side(),
        "maxGeotiffMb": MAX_GEOTIFF_BYTES // (1024 * 1024),
        "maxGeotiffBytes": MAX_GEOTIFF_BYTES,
        "maxUploadGb": round(MAX_GEOTIFF_BYTES / (1024 ** 3), 2),
    }


@router.get("/local/debug")
def local_debug(db: Session = Depends(get_db)):
    _require_dda()
    return library_debug_info(db=db)


@router.get("/local/years")
def local_years(db: Session = Depends(get_db)):
    _require_dda()
    return {"years": scan_years(db=db), "rootPaths": [str(r) for r in get_library_roots()]}


@router.get("/local/tree")
def local_tree(db: Session = Depends(get_db)):
    _require_dda()
    return scan_tree(db)


@router.get("/local/images")
def local_images(
    year: Optional[int] = Query(None),
    zone_id: Optional[int] = Query(None),
    folder_id: Optional[int] = Query(None),
    legacy_only: Optional[bool] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_dda()
    entries = scan_images(
        db=db, year=year, zone_id=zone_id, folder_id=folder_id,
        query=q, legacy_only=legacy_only,
    )
    return [entry_to_dict(e) for e in entries]


@router.get("/local/images/detail")
def local_image_detail(path: str = Query(..., description="Relative path e.g. zone/folder/2025/aerial.tif"), db: Session = Depends(get_db)):
    _require_dda()
    entries = scan_images(db=db)
    norm = path.replace("\\", "/")
    match = next((e for e in entries if e.path == norm), None)
    if not match:
        safe_resolve(path)
        match = next((e for e in scan_images(db=db) if e.path == norm), None)
    if not match:
        raise HTTPException(status_code=404, detail="Image not found in library scan")
    return entry_to_dict(match, include_meta=True)


@router.get("/local/thumb")
def local_thumb(path: str = Query(...)):
    _require_dda()
    try:
        thumb = get_or_build_thumb(path)
        return FileResponse(thumb, media_type="image/png")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Thumb endpoint fallback for %s: %s", path, exc)
        from .config import LOCAL_THUMB_CACHE
        from .geotiff_io import write_placeholder_png

        cache = LOCAL_THUMB_CACHE / "fallback.png"
        write_placeholder_png(cache, Path(path).name)
        return FileResponse(cache, media_type="image/png")


@router.post("/local/upload")
async def local_upload(
    request: Request,
    file: UploadFile = File(...),
    zone_id: int = Form(...),
    folder_id: int = Form(...),
    year: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Upload GeoTIFF into library_sources/{zone}/{folder}/{year}/."""
    _require_dda()
    require_min_role(user, db, "uploader")
    if year < 1990 or year > 2100:
        raise HTTPException(status_code=400, detail="year must be between 1990 and 2100")

    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    folder = db.query(DdaVillage).filter(
        DdaVillage.id == folder_id, DdaVillage.zone_id == zone_id
    ).first()
    if not zone or not folder or not zone.slug or not folder.slug:
        raise HTTPException(status_code=400, detail="Invalid zone or folder")

    original = _safe_basename(file.filename or "upload")
    ext = Path(original).suffix.lower()
    from .config import ALLOWED_EXTENSIONS
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    root = get_writable_library_root()
    dest = build_upload_dest(root, zone.slug, folder.slug, year, original)
    size = await stream_upload_to_file(file, dest, max_upload_bytes_for_extension(ext))
    rel = dest.relative_to(root).as_posix()
    logger.info("Library upload: %s (%d bytes) -> %s", original, size, dest)

    _upsert_file_index(db, rel, zone.id, folder.id, year, user.id)

    entries = scan_images(db=db, year=year, zone_id=zone_id, folder_id=folder_id)
    match = next((e for e in entries if e.path == rel or e.filename == dest.name), None)
    if match:
        return {"status": "success", "path": match.path, "image": entry_to_dict(match)}
    return {
        "status": "success",
        "path": rel,
        "fileSizeBytes": size,
        "writablePath": str(dest),
    }


@router.post("/local/reassign")
def local_reassign(
    body: ReassignBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Move a library file to zone/folder/year and update index."""
    _require_dda()
    require_min_role(user, db, "uploader")

    zone = db.query(DdaZone).filter(DdaZone.id == body.zone_id).first()
    folder = db.query(DdaVillage).filter(
        DdaVillage.id == body.folder_id, DdaVillage.zone_id == body.zone_id
    ).first()
    if not zone or not folder or not zone.slug or not folder.slug:
        raise HTTPException(status_code=400, detail="Invalid zone or folder")

    src_rel = body.path.replace("\\", "/").strip()
    src = safe_resolve(src_rel)
    root = get_writable_library_root()
    try:
        src.relative_to(root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Only files in writable library can be reassigned")

    dest = build_upload_dest(root, zone.slug, folder.slug, body.year, src.name)
    if dest.resolve() != src.resolve():
        shutil.move(str(src), str(dest))
    new_rel = dest.relative_to(root).as_posix()

    old_idx = db.query(DdaLocalFileIndex).filter(DdaLocalFileIndex.relative_path == src_rel).first()
    if old_idx:
        db.delete(old_idx)
        db.commit()
    _upsert_file_index(db, new_rel, zone.id, folder.id, body.year, user.id)

    entries = scan_images(db=db)
    match = next((e for e in entries if e.path == new_rel), None)
    if not match:
        raise HTTPException(status_code=500, detail="File moved but not found in scan")
    return {"status": "success", "path": new_rel, "image": entry_to_dict(match)}


@router.post("/local/rescan")
def local_rescan(db: Session = Depends(get_db)):
    _require_dda()
    tree = scan_tree(db)
    total = len(scan_images(db=db))
    years = scan_years(db=db)
    info = library_debug_info(db=db)
    logger.info("Library rescan: %d images, roots=%s", total, info.get("roots"))
    return {
        "ok": True,
        "tree": tree,
        "years": years,
        "totalImages": total,
        "rootPaths": [str(r) for r in get_library_roots()],
        "writablePath": str(get_writable_library_root()),
        "debug": info,
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

    max_side = get_detection_max_side()

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
            max_size=max_side,
            geo_bounds_path=base_file,
            user_id=user.id,
        )
    except Exception as exc:
        logger.exception("Library detection failed for %s vs %s", base_norm, comp_norm)
        raise HTTPException(status_code=500, detail=f"Detection failed: {exc}") from exc
