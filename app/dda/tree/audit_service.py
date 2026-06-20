"""Audit logging for tree mutations."""
from __future__ import annotations

import json
from typing import Any, Optional

from sqlalchemy.orm import Session

from .models import AuditLog


def log_action(
    db: Session,
    action: str,
    *,
    node_id: Optional[int] = None,
    old_value: Any = None,
    new_value: Any = None,
    action_by: str = "",
) -> None:
    entry = AuditLog(
        action=action,
        node_id=node_id,
        old_value=json.dumps(old_value, default=str) if old_value is not None else "",
        new_value=json.dumps(new_value, default=str) if new_value is not None else "",
        action_by=action_by or "",
    )
    db.add(entry)
