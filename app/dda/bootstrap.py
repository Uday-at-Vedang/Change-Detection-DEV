import logging

from fastapi import FastAPI
from sqlalchemy import text as sa_text

from ..database import engine
from .config import IS_DDA_MODE, ensure_library_dirs, get_storage_root, is_hf_hosted
from .admin_routes import router as admin_router
from .jobs_routes import router as jobs_router
from .library_routes import router as library_router
from .local_routes import router as local_router
from .reports_routes import router as reports_router
from .review_routes import router as review_router
from .training_routes import router as training_router
from .tree.routes import router as tree_router
from .dda_auth import seed_dda_admin

logger = logging.getLogger(__name__)


def init_dda_database():
    """Run DDA-specific startup tasks (dirs, seed, migrations)."""
    if not IS_DDA_MODE:
        return
    ensure_library_dirs()
    get_storage_root().mkdir(parents=True, exist_ok=True)
    try:
        with engine.connect() as conn:
            for stmt in (
                "ALTER TABLE users ADD COLUMN role VARCHAR(32) DEFAULT 'analyst'",
                "ALTER TABLE detection_runs ADD COLUMN after_full_path VARCHAR(512) DEFAULT ''",
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
    except Exception as exc:
        logger.warning("DDA schema migration skipped: %s", exc)

    from ..database import Base, SessionLocal
    from .tree.models import AuditLog, ImageLibrary, TreeNode  # noqa: F401
    try:
        Base.metadata.create_all(bind=engine, tables=[
            TreeNode.__table__,
            ImageLibrary.__table__,
            AuditLog.__table__,
        ])
    except Exception as exc:
        logger.warning("Tree table create skipped: %s", exc)

    db = SessionLocal()
    try:
        seed_dda_admin(db)
        from .tree.migration import run_tree_migration
        mig = run_tree_migration(db)
        logger.info("Tree migration: %s", mig)
        from .tree.sync_service import sync_from_filesystem
        sync_stats = sync_from_filesystem(db)
        logger.info("Filesystem sync at startup: %s", sync_stats)
        from .job_runner import reconcile_stale_jobs
        reconcile_stale_jobs(db)
    finally:
        db.close()

    try:
        from .tree.image_service import list_all_images
        from ..database import SessionLocal as SL
        sdb = SL()
        try:
            total = len(list_all_images(sdb))
        finally:
            sdb.close()
        logger.info(
            "DDA tree library ready (hosted=%s): %d images, storage=%s",
            is_hf_hosted(),
            total,
            get_storage_root(),
        )
    except Exception as exc:
        logger.warning("Tree library scan at startup failed: %s", exc)


def setup_dda(app: FastAPI) -> None:
    if not IS_DDA_MODE:
        logger.info("APP_MODE=legacy — DDA routes disabled")
        return
    app.include_router(tree_router, prefix="/api/dda", tags=["dda-tree"])
    app.include_router(library_router, prefix="/api/dda", tags=["dda"])
    app.include_router(jobs_router, prefix="/api/dda", tags=["dda-jobs"])
    app.include_router(reports_router, prefix="/api/dda", tags=["dda-reports"])
    app.include_router(review_router, prefix="/api/dda", tags=["dda-review"])
    app.include_router(training_router, prefix="/api/dda", tags=["dda-training"])
    app.include_router(admin_router, prefix="/api/dda", tags=["dda-admin"])
    app.include_router(local_router, prefix="/api/dda", tags=["dda-local"])
    logger.info("APP_MODE=dda — DDA routes enabled (tree, library, jobs, reports, review, training, admin, local)")
