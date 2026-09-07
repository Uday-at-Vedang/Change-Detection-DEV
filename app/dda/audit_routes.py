"""API for the Audit Log page — user activity trail (login/logout, uploads,
detections, reports, admin/master-data changes)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .rbac.permissions import require_module_permission
from .tree.audit_service import isoformat_ist
from .tree.models import AuditLog

router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


def _entry_dict(entry: AuditLog) -> dict:
    return {
        "id": entry.id,
        "timestamp": isoformat_ist(entry.action_date),
        "user": entry.action_by or "",
        "role": entry.role or "",
        "action": entry.action,
        "module": entry.module or "",
        "nodeId": entry.node_id,
        "oldValue": entry.old_value or "",
        "newValue": entry.new_value or "",
    }


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


@router.get("/audit-log")
def list_audit_log(
    action: str = Query(""),
    module: str = Query(""),
    role: str = Query(""),
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user: User = Depends(require_module_permission("audit_log", "view")),
    db: Session = Depends(get_db),
):
    _require_dda()
    q = db.query(AuditLog)
    if action:
        q = q.filter(AuditLog.action == action)
    if module:
        q = q.filter(AuditLog.module == module)
    if role:
        q = q.filter(AuditLog.role == role)
    start = _parse_date(from_date)
    if start:
        q = q.filter(AuditLog.action_date >= start)
    end = _parse_date(to_date, end_of_day=True)
    if end:
        q = q.filter(AuditLog.action_date <= end)

    total = q.count()
    rows = q.order_by(AuditLog.action_date.desc()).offset(offset).limit(limit).all()

    return {
        "logs": [_entry_dict(r) for r in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/audit-log/filters")
def audit_log_filters(
    user: User = Depends(require_module_permission("audit_log", "view")),
    db: Session = Depends(get_db),
):
    """Distinct action/module/role values, for populating the filter dropdowns."""
    _require_dda()
    actions = [r[0] for r in db.query(AuditLog.action).distinct().order_by(AuditLog.action) if r[0]]
    modules = [r[0] for r in db.query(AuditLog.module).distinct().order_by(AuditLog.module) if r[0]]
    roles = [r[0] for r in db.query(AuditLog.role).distinct().order_by(AuditLog.role) if r[0]]
    return {"actions": actions, "modules": modules, "roles": roles}
