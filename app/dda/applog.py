"""In-memory + file application log so the Logs page can view recent events."""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from ..database import DATA_DIR

_LOCK = threading.Lock()
_BUFFER: deque = deque(maxlen=4000)
_HANDLER: Optional["RingLogHandler"] = None

LOG_DIR = DATA_DIR / "logs"
LOG_FILE = LOG_DIR / "app.log"

_LEVELS = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}


class RingLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        created = datetime.fromtimestamp(record.created, tz=timezone.utc)
        entry = {
            "ts": created.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": msg,
        }
        with _LOCK:
            _BUFFER.append(entry)


def install_app_log_handler() -> None:
    """Attach once to the root logger (and uvicorn access) for the Logs UI."""
    global _HANDLER
    if _HANDLER is not None:
        return
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

    handler = RingLogHandler(level=logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _HANDLER = handler

    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.INFO or root.level == logging.NOTSET:
        root.setLevel(logging.INFO)

    file_handler = None
    try:
        file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        ))
        root.addHandler(file_handler)
    except OSError:
        file_handler = None

    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addHandler(handler)
        if file_handler:
            logging.getLogger(name).addHandler(file_handler)


def query_logs(
    *,
    level: str = "",
    q: str = "",
    logger_name: str = "",
    limit: int = 200,
    offset: int = 0,
) -> dict:
    min_level = _LEVELS.get((level or "").upper(), 0)
    needle = (q or "").strip().lower()
    logger_f = (logger_name or "").strip().lower()
    with _LOCK:
        rows = list(_BUFFER)
    rows.reverse()
    filtered: List[dict] = []
    for row in rows:
        if min_level and _LEVELS.get(row["level"], 0) < min_level:
            continue
        if logger_f and logger_f not in (row.get("logger") or "").lower():
            continue
        if needle and needle not in (row.get("message") or "").lower() and needle not in (row.get("logger") or "").lower():
            continue
        filtered.append(row)
    total = len(filtered)
    page = filtered[offset:offset + limit]
    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "logs": page,
        "file": str(LOG_FILE) if LOG_FILE.exists() else "",
    }


def clear_logs() -> dict:
    with _LOCK:
        _BUFFER.clear()
    truncated = False
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        LOG_FILE.write_text("", encoding="utf-8")
        truncated = True
    except OSError:
        pass
    return {"ok": True, "cleared": True, "fileTruncated": truncated}


def log_file_path() -> Path:
    return LOG_FILE
