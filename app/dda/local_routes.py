"""API for reading images from local library_sources/ year folders."""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .config import IS_DDA_MODE, geotiff_io_available, get_library_roots
from .local_library import (
    entry_to_dict,
    get_or_build_thumb,
    library_debug_info,
    safe_resolve,
    scan_images,
    scan_years,
)

router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled on this server")


@router.get("/local/config")
def local_library_config():
    _require_dda()
    roots = [str(r) for r in get_library_roots()]
    return {
        "source": "local_folder",
        "rootPath": roots[0] if roots else "",
        "rootPaths": roots,
        "instructions": (
            "Copy .tif / .tiff images into library_sources/YEAR/ inside the project folder "
            "(e.g. change_detection_webapp/library_sources/2025/), then click Refresh. "
            "Run locally with: python run.py (DDA mode is enabled automatically)."
        ),
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
    match = next((e for e in entries if e.path == path.replace("\\", "/")), None)
    if not match:
        safe_resolve(path)  # raises 404 if missing
        entries = scan_images()
        match = next((e for e in entries if e.path == path.replace("\\", "/")), None)
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


@router.post("/local/rescan")
def local_rescan():
    _require_dda()
    years = scan_years()
    total = sum(y["imageCount"] for y in years)
    return {"ok": True, "years": years, "totalImages": total, "rootPaths": [str(r) for r in get_library_roots()]}
