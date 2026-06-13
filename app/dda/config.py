import os
from pathlib import Path

from ..database import DATA_DIR

APP_MODE = os.environ.get("APP_MODE", "legacy").strip().lower()
IS_DDA_MODE = APP_MODE == "dda"

LIBRARY_DIR = DATA_DIR / "library"
THUMBS_DIR = LIBRARY_DIR / "thumbs"
PREVIEWS_DIR = LIBRARY_DIR / "previews"

# GeoTIFF upload limit (DDA responsible for suitable resolution per SOW)
MAX_GEOTIFF_BYTES = int(os.environ.get("MAX_GEOTIFF_MB", "500")) * 1024 * 1024

ALLOWED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def ensure_library_dirs() -> None:
    for d in (LIBRARY_DIR, THUMBS_DIR, PREVIEWS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass


def geotiff_io_available() -> bool:
    try:
        import rasterio  # noqa: F401
        return True
    except ImportError:
        return False
