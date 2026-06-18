"""DDA session users and RBAC (Phase 7)."""
from __future__ import annotations

import os
from typing import Iterable, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..auth import get_or_create_guest_user, get_user_by_id, get_user_from_token
from ..database import get_db
from ..models import User

DDA_SESSION_COOKIE = "dda_session_id"
COOKIE_NAME = "satellite_token"

ROLE_RANK = {
    "viewer": 0,
    "uploader": 1,
    "analyst": 2,
    "admin": 3,
}

TRAINING_EXPORT_KEY = os.environ.get("DDA_TRAINING_EXPORT_KEY", "").strip()


def _session_email(session_id: str) -> str:
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")[:64]
    return f"session-{safe}@dda.local"


def get_user_role(db: Session, user: User) -> str:
    role = getattr(user, "role", None)
    if role:
        return str(role)
    try:
        from sqlalchemy import text as sa_text
        row = db.execute(
            sa_text("SELECT role FROM users WHERE id = :uid"),
            {"uid": user.id},
        ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return "analyst"


def get_dda_user(request: Request, db: Session) -> User:
    """Resolve user: JWT login > per-browser session > shared guest."""
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
        user = get_user_from_token(token, db)
        if user:
            return user

    token = request.cookies.get(COOKIE_NAME)
    if token:
        user = get_user_from_token(token, db)
        if user and not user.email.startswith("__guest__"):
            return user

    session_id = request.cookies.get(DDA_SESSION_COOKIE)
    if session_id:
        email = _session_email(session_id)
        from ..auth import get_user_by_email, get_password_hash
        user = get_user_by_email(db, email)
        if user:
            return user
        user = User(
            email=email,
            hashed_password=get_password_hash("session-not-used"),
            full_name=f"Session {session_id[:8]}",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        try:
            from sqlalchemy import text as sa_text
            db.execute(
                sa_text("UPDATE users SET role = 'analyst' WHERE id = :uid"),
                {"uid": user.id},
            )
            db.commit()
        except Exception:
            db.rollback()
        return user

    return get_or_create_guest_user(db)


def current_dda_user(request: Request, db: Session = Depends(get_db)) -> User:
    return get_dda_user(request, db)


def require_min_role(user: User, db: Session, minimum: str) -> None:
    role = get_user_role(db, user)
    need = ROLE_RANK.get(minimum, 99)
    have = ROLE_RANK.get(role, 0)
    if have < need:
        raise HTTPException(
            status_code=403,
            detail=f"Role '{role}' cannot perform this action (requires {minimum} or higher).",
        )


def require_admin_or_key(request: Request, user: User, db: Session) -> None:
    key = request.headers.get("x-dda-training-key", "").strip()
    if TRAINING_EXPORT_KEY and key == TRAINING_EXPORT_KEY:
        return
    require_min_role(user, db, "admin")


def seed_dda_admin(db: Session) -> None:
    """Optional admin account from env (dev/UAT)."""
    email = os.environ.get("DDA_ADMIN_EMAIL", "").strip()
    password = os.environ.get("DDA_ADMIN_PASSWORD", "").strip()
    if not email or not password:
        return
    from ..auth import get_password_hash, get_user_by_email
    user = get_user_by_email(db, email)
    if not user:
        user = User(
            email=email,
            hashed_password=get_password_hash(password),
            full_name=os.environ.get("DDA_ADMIN_NAME", "DDA Admin"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    try:
        from sqlalchemy import text as sa_text
        db.execute(
            sa_text("UPDATE users SET role = 'admin' WHERE id = :uid"),
            {"uid": user.id},
        )
        db.commit()
    except Exception:
        db.rollback()
