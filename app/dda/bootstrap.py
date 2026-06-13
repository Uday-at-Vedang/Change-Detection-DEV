import logging

from fastapi import FastAPI
from sqlalchemy import text as sa_text

from ..database import engine
from .config import IS_DDA_MODE, ensure_library_dirs
from .library_routes import router as library_router
from .seed import seed_delhi_hierarchy

logger = logging.getLogger(__name__)


def init_dda_database():
    """Run DDA-specific startup tasks (dirs, seed, migrations)."""
    if not IS_DDA_MODE:
        return
    ensure_library_dirs()
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


def setup_dda(app: FastAPI) -> None:
    if not IS_DDA_MODE:
        logger.info("APP_MODE=legacy — DDA routes disabled")
        return
    app.include_router(library_router, prefix="/api/dda", tags=["dda"])
    logger.info("APP_MODE=dda — DDA library routes enabled")
