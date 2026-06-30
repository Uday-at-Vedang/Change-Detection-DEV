import os
from pathlib import Path
from typing import List

from ..database import DATA_DIR

APP_MODE_RAW = os.environ.get("APP_MODE", "").strip().lower()
_SPACE_ID = os.environ.get("SPACE_ID", "").strip().lower()


def is_hf_hosted() -> bool:
    return bool(_SPACE_ID)


def _is_dda_mode() -> bool:
    if APP_MODE_RAW == "legacy":
        return False
    if APP_MODE_RAW == "dda":
        return True
    # APP_MODE unset: auto-enable on satdetect-dev only (production satdetect stays legacy)
    return _SPACE_ID.endswith("/satdetect-dev")


def get_public_base_url() -> str:
    """Public URL for deep links (email, share). Override with PUBLIC_BASE_URL."""
    explicit = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if explicit:
        return explicit
    if _SPACE_ID:
        return f"https://{_SPACE_ID.replace('/', '-')}.hf.space"
    return "http://localhost:7860"


IS_DDA_MODE = _is_dda_mode()
APP_MODE = APP_MODE_RAW or ("dda" if IS_DDA_MODE else "legacy")


# Project root: change_detection_webapp/
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def get_writable_library_root() -> Path:
    """Persistent folder for uploads (HF: /home/appuser/data/library_sources)."""
    return (DATA_DIR / "library_sources").resolve()


def get_storage_root() -> Path:
    """Tree library root (doc: root_directory / STORAGE_ROOT)."""
    explicit = os.environ.get("STORAGE_ROOT", "").strip()
    if explicit:
        return Path(explicit).resolve()
    if os.environ.get("LOCAL_LIBRARY_ROOT"):
        return Path(os.environ["LOCAL_LIBRARY_ROOT"]).resolve()
    return get_writable_library_root()


def get_library_roots() -> List[Path]:
    """Folders scanned for library images (tree storage root)."""
    roots: List[Path] = []
    sr = get_storage_root()
    if sr not in roots:
        roots.append(sr)
    if os.environ.get("LOCAL_LIBRARY_ROOT"):
        p = Path(os.environ["LOCAL_LIBRARY_ROOT"]).resolve()
        if p not in roots:
            roots.append(p)
    if is_hf_hosted():
        wr = get_writable_library_root()
        if wr not in roots:
            roots.append(wr)
    for candidate in (get_writable_library_root(), PROJECT_ROOT / "library_sources"):
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

# Library upload limit (default 15 GB; override with MAX_GEOTIFF_MB / MAX_IMAGE_MB)
_MAX_UPLOAD_MB_DEFAULT = "15360"  # 15 * 1024
MAX_GEOTIFF_BYTES = int(os.environ.get("MAX_GEOTIFF_MB", _MAX_UPLOAD_MB_DEFAULT)) * 1024 * 1024

# PNG/JPEG use the same cap unless MAX_IMAGE_MB is set separately
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_MB", _MAX_UPLOAD_MB_DEFAULT)) * 1024 * 1024

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


def get_detection_max_side() -> int:
    """Max pixel dimension for GeoTIFF load + detection pipeline (higher = sharper, more RAM)."""
    default = "2048" if is_hf_hosted() else "4096"
    try:
        value = int(os.environ.get("DETECTION_MAX_SIDE", default))
    except ValueError:
        value = int(default)
    return max(1024, min(8192, value))
