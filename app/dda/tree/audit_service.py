"""Audit logging for tree mutations, and the app-wide activity/exception trail."""
from __future__ import annotations

import json
import traceback as traceback_module
from datetime import timedelta, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditLog, ExceptionLog

_IST = timezone(timedelta(hours=5, minutes=30))


def isoformat_ist(dt) -> Optional[str]:
    """Shared IST timestamp formatter for the Audit Log / Exception Log pages."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def log_action(
    db: Session,
    action: str,
    *,
    node_id: Optional[int] = None,
    old_value: Any = None,
    new_value: Any = None,
    action_by: str = "",
    role: str = "",
    module: str = "",
) -> None:
    entry = AuditLog(
        action=action,
        node_id=node_id,
        old_value=json.dumps(old_value, default=str) if old_value is not None else "",
        new_value=json.dumps(new_value, default=str) if new_value is not None else "",
        action_by=action_by or "",
        role=role or "",
        module=module or "",
    )
    db.add(entry)


def log_exception(
    db: Session,
    *,
    exc: BaseException,
    path: str = "",
    method: str = "",
    status_code: int = 500,
    handled: bool = False,
    user_email: Optional[str] = None,
) -> None:
    entry = ExceptionLog(
        exception_type=type(exc).__name__,
        message=str(exc)[:4000],
        path=path,
        method=method,
        status_code=status_code,
        handled=handled,
        traceback="".join(traceback_module.format_exception(type(exc), exc, exc.__traceback__))[:8000],
        user_email=user_email,
    )
    db.add(entry)
    db.commit()
