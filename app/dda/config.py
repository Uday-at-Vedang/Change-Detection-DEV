import os
from pathlib import Path
from typing import List

from ..database import DATA_DIR

APP_MODE = os.environ.get("APP_MODE", "legacy").strip().lower()
IS_DDA_MODE = APP_MODE == "dda"

# Project root: change_detection_webapp/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_library_roots() -> List[Path]:
    """All folders scanned for year-based images (project + writable data copy)."""
    roots: List[Path] = []
    if os.environ.get("LOCAL_LIBRARY_ROOT"):
        roots.append(Path(os.environ["LOCAL_LIBRARY_ROOT"]).resolve())
    for candidate in (PROJECT_ROOT / "library_sources", DATA_DIR / "library_sources"):
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


# Primary root (shown in UI) — first entry from get_library_roots()
LOCAL_LIBRARY_ROOT = get_library_roots()[0]

LIBRARY_DIR = DATA_DIR / "library"
THUMBS_DIR = LIBRARY_DIR / "thumbs"
PREVIEWS_DIR = LIBRARY_DIR / "previews"
LOCAL_THUMB_CACHE = DATA_DIR / "library_cache" / "thumbs"

# GeoTIFF library upload limit (default 2 GB; override with MAX_GEOTIFF_MB on HF dev Space)
MAX_GEOTIFF_BYTES = int(os.environ.get("MAX_GEOTIFF_MB", "2048")) * 1024 * 1024

# Raster sidecar formats (PNG/JPEG) — smaller cap for library uploads
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_MB", "50")) * 1024 * 1024

ALLOWED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def ensure_library_dirs() -> None:
    for root in get_library_roots():
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    for d in (LIBRARY_DIR, THUMBS_DIR, PREVIEWS_DIR, LOCAL_THUMB_CACHE):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def ensure_local_year_folders() -> None:
    """Create default year folders under each library root if missing."""
    from datetime import datetime
    current = datetime.now().year
    for root in get_library_roots():
        for year in (current - 1, current, current + 1):
            try:
                (root / str(year)).mkdir(parents=True, exist_ok=True)
            except OSError:
                pass


def max_upload_bytes_for_extension(ext: str) -> int:
    if ext in (".tif", ".tiff"):
        return MAX_GEOTIFF_BYTES
    return MAX_IMAGE_BYTES


def geotiff_io_available() -> bool:
    try:
        import rasterio  # noqa: F401
        return True
    except ImportError:
        return False
