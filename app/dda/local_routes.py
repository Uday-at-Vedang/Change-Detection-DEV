"""API for reading images from local library_sources/ year folders."""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image
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
    entry_to_dict,
    get_or_build_thumb,
    library_debug_info,
    safe_resolve,
    scan_images,
    scan_years,
)
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


@router.get("/local/config")
def local_library_config():
    _require_dda()
    roots = [str(r) for r in get_library_roots()]
    writable = str(get_writable_library_root())
    hosted = is_hf_hosted()
    if hosted:
        instructions = (
            "On Hugging Face, images must be uploaded below (saved to persistent storage) "
            "or copied into the writable folder shown. Files on your PC are not visible here."
        )
    else:
        instructions = (
            "Copy .tif images into library_sources/YEAR/ in your project folder, then Refresh. "
            "Or use Upload to save into data/library_sources/."
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
def local_debug():
    _require_dda()
    return library_debug_info()


@router.get("/local/years")
def local_years():
    _require_dda()
    return {"years": scan_years(), "rootPaths": [str(r) for r in get_library_roots()]}


@router.get("/local/images")
def local_images(
    year: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
):
    _require_dda()
    entries = scan_images(year=year, query=q)
    return [entry_to_dict(e) for e in entries]


@router.get("/local/images/detail")
def local_image_detail(path: str = Query(..., description="Relative path e.g. 2025/aerial.tif")):
    _require_dda()
    entries = scan_images()
    norm = path.replace("\\", "/")
    match = next((e for e in entries if e.path == norm), None)
    if not match:
        safe_resolve(path)
        match = next((e for e in scan_images() if e.path == norm), None)
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
    year: int = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Upload GeoTIFF into persistent library_sources/YEAR/ (required on HF)."""
    _require_dda()
    require_min_role(user, db, "uploader")
    if year < 1990 or year > 2100:
        raise HTTPException(status_code=400, detail="year must be between 1990 and 2100")

    original = _safe_basename(file.filename or "upload")
    ext = Path(original).suffix.lower()
    from .config import ALLOWED_EXTENSIONS
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}")

    root = get_writable_library_root()
    dest = root / str(year) / original
    if dest.exists():
        stem = Path(original).stem
        suffix = Path(original).suffix
        n = 1
        while dest.exists():
            dest = root / str(year) / f"{stem}_{n}{suffix}"
            n += 1

    size = await stream_upload_to_file(file, dest, max_upload_bytes_for_extension(ext))
    rel = dest.relative_to(root).as_posix()
    logger.info("Library upload: %s (%d bytes) -> %s", original, size, dest)

    entries = scan_images(year=year)
    match = next((e for e in entries if e.path == rel or e.filename == dest.name), None)
    if match:
        return {"status": "success", "path": match.path, "image": entry_to_dict(match)}
    return {
        "status": "success",
        "path": f"{year}/{dest.name}",
        "fileSizeBytes": size,
        "writablePath": str(dest),
    }


@router.post("/local/rescan")
def local_rescan():
    _require_dda()
    years = scan_years()
    total = sum(y["imageCount"] for y in years)
    info = library_debug_info()
    logger.info("Library rescan: %d images, roots=%s", total, info.get("roots"))
    return {
        "ok": True,
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
    """Run change detection on two library images by relative path (e.g. 2025/aerial.tif)."""
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

    # Match dimensions so registration and overlay align with the before image
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
