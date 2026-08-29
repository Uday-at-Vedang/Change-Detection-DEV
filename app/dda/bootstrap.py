import logging

from fastapi import FastAPI
from sqlalchemy import text as sa_text

from ..database import engine
from .config import IS_DDA_MODE, ensure_library_dirs, get_storage_root, is_hf_hosted
from .admin_routes import router as admin_router
from .dashboard_routes import router as dashboard_router
from .jobs_routes import router as jobs_router
from .library_routes import router as library_router
from .local_routes import router as local_router
from .reports_routes import router as reports_router
from .review_routes import router as review_router
from .training_routes import router as training_router
from .tree.routes import router as tree_router
from .rbac.routes import router as rbac_router
from .masters_routes import router as masters_router
from .logs_routes import router as logs_router
from .dda_auth import seed_dda_admin

logger = logging.getLogger(__name__)


def _migrate_detection_jobs_nullable() -> None:
    """SQLite legacy schema required image asset FKs; tree library jobs use paths only.

    SQLite-only: rebuilds the table via SQLite's PRAGMA/copy-and-rename
    pattern because SQLite can't ALTER COLUMN to drop NOT NULL directly.
    Irrelevant on MySQL/PostgreSQL — a fresh create_all() there already
    creates the column with the current (nullable) definition, and neither
    speaks SQLite's PRAGMA dialect, so skip cleanly instead of erroring.
    """
    if engine.dialect.name != "sqlite":
        return
    try:
        with engine.connect() as conn:
            rows = conn.execute(sa_text("PRAGMA table_info(dda_detection_jobs)")).fetchall()
            if not rows:
                return
            by_name = {r[1]: r for r in rows}
            base_col = by_name.get("base_image_id")
            if not base_col or base_col[3] == 0:
                return
            logger.info("Migrating dda_detection_jobs to allow NULL image IDs for path-based jobs")
            conn.execute(sa_text("PRAGMA foreign_keys=OFF"))
            conn.execute(sa_text("""
                CREATE TABLE dda_detection_jobs_new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    status VARCHAR(32),
                    base_image_id INTEGER,
                    comparison_image_id INTEGER,
                    method VARCHAR(64),
                    params_json TEXT,
                    run_id INTEGER,
                    error_message TEXT,
                    notify_email VARCHAR(255),
                    created_by INTEGER,
                    started_at DATETIME,
                    completed_at DATETIME,
                    created_at DATETIME,
                    FOREIGN KEY(base_image_id) REFERENCES dda_image_assets (id),
                    FOREIGN KEY(comparison_image_id) REFERENCES dda_image_assets (id),
                    FOREIGN KEY(run_id) REFERENCES detection_runs (id),
                    FOREIGN KEY(created_by) REFERENCES users (id)
                )
            """))
            conn.execute(sa_text("""
                INSERT INTO dda_detection_jobs_new (
                    id, status, base_image_id, comparison_image_id, method, params_json,
                    run_id, error_message, notify_email, created_by,
                    started_at, completed_at, created_at
                )
                SELECT
                    id, status, base_image_id, comparison_image_id, method, params_json,
                    run_id, error_message, notify_email, created_by,
                    started_at, completed_at, created_at
                FROM dda_detection_jobs
            """))
            conn.execute(sa_text("DROP TABLE dda_detection_jobs"))
            conn.execute(sa_text("ALTER TABLE dda_detection_jobs_new RENAME TO dda_detection_jobs"))
            conn.execute(sa_text("PRAGMA foreign_keys=ON"))
            conn.commit()
    except Exception as exc:
        logger.warning("Detection jobs nullable migration skipped: %s", exc)


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
                "ALTER TABLE users ADD COLUMN role_id INTEGER DEFAULT NULL",
                "ALTER TABLE users ADD COLUMN totp_secret VARCHAR(64) DEFAULT NULL",
                "ALTER TABLE users ADD COLUMN totp_enabled INTEGER DEFAULT 0",
                "ALTER TABLE users ADD COLUMN totp_confirmed_at DATETIME DEFAULT NULL",
                "ALTER TABLE users ADD COLUMN phone VARCHAR(20) DEFAULT NULL",
                "ALTER TABLE detection_runs ADD COLUMN after_full_path VARCHAR(512) DEFAULT ''",
                "ALTER TABLE dda_roles ADD COLUMN is_active BOOLEAN DEFAULT 1",
            ):
                try:
                    conn.execute(sa_text(stmt))
                    conn.commit()
                except Exception:
                    conn.rollback()
            try:
                conn.execute(sa_text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"
                ))
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
            _migrate_detection_jobs_nullable()
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
        from .rbac.seed import seed_rbac
        try:
            seed_rbac(db)
        except Exception as exc:
            logger.warning("RBAC seed skipped: %s", exc)
        try:
            from .seed import seed_delhi_hierarchy
            seed_delhi_hierarchy(db)
        except Exception as exc:
            logger.warning("Zone/village seed skipped: %s", exc)
        from .tree.migration import run_tree_migration
        mig = run_tree_migration(db)
        logger.info("Tree migration: %s", mig)
        try:
            from .tree.sync_service import sync_from_filesystem
            sync_stats = sync_from_filesystem(db)
            logger.info("Filesystem sync at startup: %s", sync_stats)
        except Exception as exc:
            logger.warning("Filesystem sync at startup failed: %s", exc)
        from .auto_detect import reset_schedule_on_process_start
        reset_schedule_on_process_start()
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
    app.include_router(rbac_router, prefix="/api/dda", tags=["dda-rbac"])
    app.include_router(dashboard_router, prefix="/api/dda", tags=["dda-dashboard"])
    app.include_router(masters_router, prefix="/api/dda", tags=["dda-masters"])
    app.include_router(logs_router, prefix="/api/dda", tags=["dda-logs"])
    logger.info("APP_MODE=dda — DDA routes enabled (tree, library, jobs, reports, review, training, admin, local, rbac, dashboard, masters, logs)")
