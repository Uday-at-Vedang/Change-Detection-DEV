"""Permission resolution for the dynamic RBAC system — used by the new admin
routes/pages, and by the navbar to filter which menu items a user sees."""
from __future__ import annotations

from typing import List, Optional

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import User
from ..dda_auth import current_dda_user
from .models import MenuItem, Module, Role, RolePermission


def get_user_role_row(user: User, db: Session) -> Optional[Role]:
    """Resolve the user's Role row: by role_id if backfilled, else by matching
    the legacy `User.role` string (case-insensitive) — covers any user created
    before the RBAC seed ran."""
    if user.role_id:
        role = db.query(Role).filter(Role.id == user.role_id).first()
        if role:
            return role
    legacy = (user.role or "").strip().lower()
    if legacy:
        return db.query(Role).filter(Role.name == legacy).first()
    return None


def get_role_rank(user: User, db: Session) -> Optional[int]:
    role = get_user_role_row(user, db)
    return role.rank if role else None


def user_can(user: User, db: Session, module_key: str, action: str = "view") -> bool:
    """action: 'view' | 'create' | 'edit' | 'delete'."""
    role = get_user_role_row(user, db)
    if not role:
        return False
    module = db.query(Module).filter(Module.key == module_key).first()
    if not module:
        return False
    perm = (
        db.query(RolePermission)
        .filter(RolePermission.role_id == role.id, RolePermission.module_id == module.id)
        .first()
    )
    if not perm:
        return False
    return bool(getattr(perm, f"can_{action}", False))


def require_module_permission(module_key: str, action: str = "view"):
    """FastAPI dependency factory — 403s unless the current user's role has
    `can_<action>` on `module_key`. Mirrors require_min_role's call shape."""
    def _dependency(
        user: User = Depends(current_dda_user),
        db: Session = Depends(get_db),
    ) -> User:
        if not user_can(user, db, module_key, action):
            raise HTTPException(
                status_code=403,
                detail=f"Missing '{action}' permission on '{module_key}'.",
            )
        return user
    return _dependency


def resolve_user_menu(user: User, db: Session) -> List[MenuItem]:
    """Top-level menu items the user's role can view, in display order, each
    with a `.visible_children` list attached (set as a plain transient
    attribute, not persisted) for items that have sub-items — the navbar
    renders those as a collapsible group like "Configuration" > Roles &
    Users / App Modules / Menu Management.

    Items with no linked module (module_id is None) are always shown, except
    a group item ("Configuration") which is only shown once it has at least
    one visible child — otherwise a role with no admin access would see an
    empty, dead group header."""

    def _visible(item: MenuItem) -> bool:
        if item.module_id is None:
            return True
        module = db.query(Module).filter(Module.id == item.module_id).first()
        return bool(module and user_can(user, db, module.key, "view"))

    top_level = (
        db.query(MenuItem)
        .filter(MenuItem.is_active.is_(True), MenuItem.parent_id.is_(None))
        .order_by(MenuItem.sort_order)
        .all()
    )

    visible = []
    for item in top_level:
        children = (
            db.query(MenuItem)
            .filter(MenuItem.is_active.is_(True), MenuItem.parent_id == item.id)
            .order_by(MenuItem.sort_order)
            .all()
        )
        item.visible_children = [c for c in children if _visible(c)]
        is_group = bool(children)
        if (item.visible_children if is_group else _visible(item)):
            visible.append(item)
    return visible
