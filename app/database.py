import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = Path(__file__).resolve().parent

# Use /home/appuser/data on HF Spaces (writable), fall back to local data/ dir
_home_data = Path.home() / "data"
_local_data = BASE_DIR.parent / "data"
DATA_DIR = _home_data if os.environ.get("SPACE_ID") else _local_data
try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass  # avoid crashing if read-only or permission issue (e.g. some HF environments)

DB_PATH = DATA_DIR / "satellite_app.db"

DATABASE_URL = os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH}")

# Render gives postgres:// but SQLAlchemy 2.x requires postgresql://
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # timeout = busy wait while another connection holds the write lock
    # (progress updates vs the long-running detection worker).
    connect_args = {"check_same_thread": False, "timeout": 60.0}

# pool_pre_ping: checks each connection before use and transparently reconnects
# if it's gone stale — matters for a remote DB (MySQL/Postgres) reached over
# the network, where idle connections can be dropped by the server or a
# firewall; irrelevant (and skipped) for local SQLite.
engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args,
    pool_pre_ping=not DATABASE_URL.startswith("sqlite"),
)


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _connection_record):
    if not DATABASE_URL.startswith("sqlite"):
        return
    cur = dbapi_conn.cursor()
    try:
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=60000")
        cur.execute("PRAGMA synchronous=NORMAL")
    finally:
        cur.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
