"""API for reading images from local library_sources/ year folders."""
import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse

from .config import (
    IS_DDA_MODE,
    geotiff_io_available,
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
        raise HTTPException(status_code=500, detail=f"Thumbnail failed: {exc}") from exc


@router.post("/local/upload")
async def local_upload(
    file: UploadFile = File(...),
    year: int = Form(...),
):
    """Upload GeoTIFF into persistent library_sources/YEAR/ (required on HF)."""
    _require_dda()
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
