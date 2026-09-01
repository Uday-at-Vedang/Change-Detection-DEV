"""SQLAlchemy helpers that behave the same on SQLite and MySQL."""
from __future__ import annotations

from sqlalchemy import or_


def active_node_clause(column):
    """Folders that should appear in the library.

    SQLite historically stored Python True as 1. MySQL BOOLEAN is TINYINT(1).
    A dump/import can also leave NULL, which ``== True`` would hide — after
    the MySQL migration that dropped images from pairing and the image grid.
    Soft-deleted rows are 0 / False and stay hidden.
    """
    return or_(column.is_(True), column == 1, column.is_(None))
