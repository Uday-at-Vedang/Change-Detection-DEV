import logging

from fastapi import FastAPI
from sqlalchemy import text as sa_text

from ..database import engine
from .config import IS_DDA_MODE, ensure_library_dirs, is_hf_hosted
from .admin_routes import router as admin_router
from .hierarchy_routes import router as hierarchy_router
from .jobs_routes import router as jobs_router
from .library_routes import router as library_router
from .local_routes import router as local_router
from .reports_routes import router as reports_router
from .review_routes import router as review_router
from .training_routes import router as training_router
from .seed import seed_delhi_hierarchy
from .dda_auth import seed_dda_admin

logger = logging.getLogger(__name__)


def init_dda_database():
    """Run DDA-specific startup tasks (dirs, seed, migrations)."""
    if not IS_DDA_MODE:
        return
    ensure_library_dirs()
    try:
        with engine.connect() as conn:
            for stmt in (
                "ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'analyst'",
                "ALTER TABLE detection_runs ADD COLUMN after_full_path VARCHAR(512) DEFAULT ''",
                "ALTER TABLE dda_zones ADD COLUMN slug VARCHAR(64)",
                "ALTER TABLE dda_villages ADD COLUMN slug VARCHAR(64)",
            ):
                try:
                    conn.execute(sa_text(stmt))
                    conn.commit()
                except Exception:
                    conn.rollback()
            try:
                conn.execute(sa_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_dda_region_reviews_run_region "
                    "ON dda_region_reviews (run_id, region_id)"
                ))
                conn.commit()
            except Exception:
                conn.rollback()
            try:
                conn.execute(sa_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_dda_zones_slug ON dda_zones (slug)"
                ))
                conn.commit()
            except Exception:
                conn.rollback()
    except Exception as exc:
        logger.warning("DDA schema migration skipped: %s", exc)

    from ..database import Base, SessionLocal
    try:
        from .models import DdaLocalFileIndex  # noqa: F401
        Base.metadata.create_all(bind=engine, tables=[DdaLocalFileIndex.__table__])
    except Exception as exc:
        logger.warning("DdaLocalFileIndex table create skipped: %s", exc)

    db = SessionLocal()
    try:
        seed_delhi_hierarchy(db)
        seed_dda_admin(db)
        from .library_migration import (
            backfill_slugs,
            ensure_legacy_zone,
            ensure_zone_folder_dirs,
            migrate_legacy_flat_years,
        )
        backfill_slugs(db)
        ensure_legacy_zone(db)
        migrate_legacy_flat_years(db)
        ensure_zone_folder_dirs(db)
        from .job_runner import reconcile_stale_jobs
        reconcile_stale_jobs(db)
    finally:
        db.close()

    try:
        from .local_library import library_debug_info, scan_images
        from ..database import SessionLocal as SL
        sdb = SL()
        try:
            info = library_debug_info(db=sdb)
            total = len(scan_images(db=sdb))
        finally:
            sdb.close()
        logger.info(
            "DDA library ready (hosted=%s): %d images, writable=%s",
            is_hf_hosted(),
            total,
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
    app.include_router(review_router, prefix="/api/dda", tags=["dda-review"])
    app.include_router(training_router, prefix="/api/dda", tags=["dda-training"])
    app.include_router(admin_router, prefix="/api/dda", tags=["dda-admin"])
    app.include_router(hierarchy_router, prefix="/api/dda", tags=["dda-hierarchy"])
    app.include_router(local_router, prefix="/api/dda", tags=["dda-local"])
    logger.info("APP_MODE=dda — DDA routes enabled (library, jobs, reports, review, training, admin, hierarchy, local)")
