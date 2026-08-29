"""API for the application Logs page."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse

from ..models import User
from .applog import clear_logs, log_file_path, query_logs
from .rbac.permissions import require_module_permission

router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


@router.get("/logs")
def list_logs(
    level: str = Query(""),
    q: str = Query(""),
    logger: str = Query(""),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_module_permission("logs", "view")),
):
    _require_dda()
    return query_logs(level=level, q=q, logger_name=logger, limit=limit, offset=offset)


@router.get("/logs/download")
def download_logs(user: User = Depends(require_module_permission("logs", "view"))):
    _require_dda()
    path = log_file_path()
    if path.exists() and path.stat().st_size:
        return FileResponse(path, filename="app.log", media_type="text/plain")
    data = query_logs(limit=1000, offset=0)
    body = "\n".join(
        f"{row['ts']} {row['level']} [{row['logger']}] {row['message']}"
        for row in reversed(data["logs"])
    ) or "(no log entries yet)"
    return PlainTextResponse(body, headers={"Content-Disposition": 'attachment; filename="app.log"'})


@router.delete("/logs")
def api_clear_logs(user: User = Depends(require_module_permission("logs", "delete"))):
    _require_dda()
    return clear_logs()
