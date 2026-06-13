import os
from pathlib import Path

from ..database import DATA_DIR

APP_MODE = os.environ.get("APP_MODE", "legacy").strip().lower()
IS_DDA_MODE = APP_MODE == "dda"

LIBRARY_DIR = DATA_DIR / "library"
THUMBS_DIR = LIBRARY_DIR / "thumbs"
PREVIEWS_DIR = LIBRARY_DIR / "previews"

# GeoTIFF library upload limit (default 2 GB; override with MAX_GEOTIFF_MB on HF dev Space)
MAX_GEOTIFF_BYTES = int(os.environ.get("MAX_GEOTIFF_MB", "2048")) * 1024 * 1024

# Raster sidecar formats (PNG/JPEG) — smaller cap for library uploads
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_MB", "50")) * 1024 * 1024

ALLOWED_EXTENSIONS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}


def ensure_library_dirs() -> None:
    for d in (LIBRARY_DIR, THUMBS_DIR, PREVIEWS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
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
