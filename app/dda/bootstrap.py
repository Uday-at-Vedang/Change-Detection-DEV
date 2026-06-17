import logging

from fastapi import FastAPI
from sqlalchemy import text as sa_text

from ..database import engine
from .config import IS_DDA_MODE, ensure_library_dirs, ensure_local_year_folders, is_hf_hosted
from .jobs_routes import router as jobs_router
from .library_routes import router as library_router
from .local_routes import router as local_router
from .reports_routes import router as reports_router
from .seed import seed_delhi_hierarchy

logger = logging.getLogger(__name__)


def init_dda_database():
    """Run DDA-specific startup tasks (dirs, seed, migrations)."""
    if not IS_DDA_MODE:
        return
    ensure_library_dirs()
    ensure_local_year_folders()
    try:
        with engine.connect() as conn:
            try:
                conn.execute(sa_text("ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'analyst'"))
                conn.commit()
            except Exception:
                conn.rollback()
    except Exception as exc:
        logger.warning("DDA user role migration skipped: %s", exc)

    from ..database import SessionLocal
    db = SessionLocal()
    try:
        seed_delhi_hierarchy(db)
    finally:
        db.close()

    try:
        from .local_library import library_debug_info, scan_images
        info = library_debug_info()
        logger.info(
            "DDA library ready (hosted=%s): %d images, writable=%s",
            is_hf_hosted(),
            len(scan_images()),
            info.get("roots", [{}])[0].get("path") if info.get("roots") else "?",
        )
    except Exception as exc:
        logger.warning("Library scan at startup failed: %s", exc)


def setup_dda(app: FastAPI) -> None:
    if not IS_DDA_MODE:
        logger.info("APP_MODE=legacy — DDA routes disabled")
        return
    app.include_router(library_router, prefix="/api/dda", tags=["dda"])
    app.include_router(jobs_router, prefix="/api/dda", tags=["dda-jobs"])
    app.include_router(reports_router, prefix="/api/dda", tags=["dda-reports"])
    app.include_router(local_router, prefix="/api/dda", tags=["dda-local"])
    logger.info("APP_MODE=dda — DDA routes enabled (library, jobs, reports, local folder)")
