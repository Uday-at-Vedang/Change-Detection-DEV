"""API for the Exception Log page — system errors captured by the global
FastAPI exception handlers in app/main.py."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .rbac.permissions import require_module_permission
from .tree.audit_service import isoformat_ist
from .tree.models import ExceptionLog

router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


def _entry_dict(entry: ExceptionLog, *, with_traceback: bool = False) -> dict:
    d = {
        "id": entry.id,
        "timestamp": isoformat_ist(entry.created_at),
        "type": entry.exception_type,
        "message": entry.message or "",
        "path": entry.path or "",
        "method": entry.method or "",
        "statusCode": entry.status_code,
        "handled": bool(entry.handled),
        "userEmail": entry.user_email or "",
        "userRole": getattr(entry, "user_role", None) or "",
    }
    if with_traceback:
        d["traceback"] = entry.traceback or ""
    return d


def _parse_date(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date: {value!r} (expected YYYY-MM-DD)")
    if end_of_day:
        dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
    return dt


@router.get("/exception-log")
def list_exception_log(
    exception_type: str = Query("", alias="type"),
    status_code: Optional[int] = Query(None),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_module_permission("exception_log", "view")),
    db: Session = Depends(get_db),
):
    _require_dda()
    q = db.query(ExceptionLog)
    if exception_type:
        q = q.filter(ExceptionLog.exception_type == exception_type)
    if status_code is not None:
        q = q.filter(ExceptionLog.status_code == status_code)
    start = _parse_date(from_date)
    if start:
        q = q.filter(ExceptionLog.created_at >= start)
    end = _parse_date(to_date, end_of_day=True)
    if end:
        q = q.filter(ExceptionLog.created_at <= end)

    total = q.count()
    page_rows = q.order_by(ExceptionLog.created_at.desc()).offset(offset).limit(limit).all()

    # Stat cards summarize the *filtered* result set (matching the reference UI's
    # "(this page)" labels), computed on the full filtered query, not just page_rows.
    filtered = q
    errors_500 = filtered.filter(ExceptionLog.status_code >= 500).count()
    errors_other = total - errors_500
    unhandled = filtered.filter(ExceptionLog.handled.is_(False)).count()

    return {
        "logs": [_entry_dict(r) for r in page_rows],
        "total": total,
        "limit": limit,
        "offset": offset,
        "stats": {"errors500": errors_500, "errorsOther": errors_other, "unhandled": unhandled},
    }


@router.get("/exception-log/filters")
def exception_log_filters(
    user: User = Depends(require_module_permission("exception_log", "view")),
    db: Session = Depends(get_db),
):
    _require_dda()
    types = [r[0] for r in db.query(ExceptionLog.exception_type).distinct().order_by(ExceptionLog.exception_type) if r[0]]
    codes = sorted({r[0] for r in db.query(ExceptionLog.status_code).distinct() if r[0] is not None})
    return {"types": types, "statusCodes": codes}


@router.get("/exception-log/{entry_id}")
def get_exception_log_entry(
    entry_id: int,
    user: User = Depends(require_module_permission("exception_log", "view")),
    db: Session = Depends(get_db),
):
    _require_dda()
    entry = db.query(ExceptionLog).filter(ExceptionLog.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Exception log entry not found")
    return _entry_dict(entry, with_traceback=True)


@router.delete("/exception-log/old")
def clear_old_exception_log(
    days: int = Query(30, ge=1, le=3650),
    user: User = Depends(require_module_permission("exception_log", "delete")),
    db: Session = Depends(get_db),
):
    _require_dda()
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    deleted = db.query(ExceptionLog).filter(ExceptionLog.created_at < cutoff).delete(synchronize_session=False)
    db.commit()
    return {"ok": True, "deleted": deleted}
