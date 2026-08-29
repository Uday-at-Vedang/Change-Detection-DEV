"""Seed the default roles, modules, menu items, and role permissions, and
backfill role_id on any user still only carrying the legacy role string.
Idempotent — safe to call on every startup (mirrors app/dda/seed.py)."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ...models import User
from .models import MenuItem, Module, Role, RolePermission

logger = logging.getLogger(__name__)

# (name, description, rank) — rank preserves the ordinal check dda_auth.py
# already relied on (ROLE_RANK) before this table existed.
DEFAULT_ROLES = [
    ("viewer", "Read-only access", 0),
    ("uploader", "Can view and upload images", 1),
    ("analyst", "Can upload, compare, and run detections", 2),
    ("admin", "Full system access", 3),
]

# (key, name, description)
DEFAULT_MODULES = [
    ("home", "Home", "Dashboard landing page"),
    ("library", "Master Library", "Browse and upload satellite / drone imagery"),
    ("masters", "Masters", "Zone / district and village / location master data"),
    ("detect", "Change Detection", "Compare imagery and run detections"),
    ("reports", "Reports", "Detection history, exports, and PDF reports"),
    ("logs", "Logs", "Application log viewer"),
    ("admin", "Administration", "Users, roles, modules, and menu management"),
]

# (label, url, module_key, sort_order, parent_label)
# Items with a parent_label render as a collapsible group in the sidebar
# (see templates/partials/navbar_dda.html) — group headers themselves have no
# module (url "#…" never navigated to directly) so they stay structurally
# present; the navbar only shows the group once it has a visible child.
DEFAULT_MENU_ITEMS = [
    ("Home", "/", "home", 0, None),
    ("Masters", "#masters", None, 1, None),
    ("Master Library", "/library", "library", 0, "Masters"),
    ("Zone / District", "/masters/zones", "masters", 1, "Masters"),
    ("Village / Location", "/masters/villages", "masters", 2, "Masters"),
    ("Change Detection", "/detect", "detect", 2, None),
    ("Reports", "/reports", "reports", 3, None),
    ("Logs", "/logs", "logs", 4, None),
    ("Configuration", "#", None, 5, None),
    ("Roles & Users", "/admin/roles-users", "admin", 0, "Configuration"),
    ("App Modules", "/admin/modules", "admin", 1, "Configuration"),
    ("Menu Management", "/admin/menu", "admin", 2, "Configuration"),
]

# module_key -> {role_name: (can_view, can_create, can_edit, can_delete)}
# Approximates current viewer < uploader < analyst < admin behavior.
DEFAULT_PERMISSIONS = {
    "home": {
        "viewer": (1, 0, 0, 0), "uploader": (1, 0, 0, 0),
        "analyst": (1, 0, 0, 0), "admin": (1, 1, 1, 1),
    },
    "library": {
        "viewer": (1, 0, 0, 0), "uploader": (1, 1, 0, 0),
        "analyst": (1, 1, 1, 0), "admin": (1, 1, 1, 1),
    },
    "masters": {
        "viewer": (1, 0, 0, 0), "uploader": (1, 0, 0, 0),
        "analyst": (1, 1, 1, 0), "admin": (1, 1, 1, 1),
    },
    "detect": {
        "viewer": (1, 0, 0, 0), "uploader": (1, 1, 0, 0),
        "analyst": (1, 1, 1, 0), "admin": (1, 1, 1, 1),
    },
    "reports": {
        "viewer": (1, 0, 0, 0), "uploader": (1, 0, 0, 0),
        "analyst": (1, 1, 1, 0), "admin": (1, 1, 1, 1),
    },
    "logs": {
        "viewer": (0, 0, 0, 0), "uploader": (0, 0, 0, 0),
        "analyst": (0, 0, 0, 0), "admin": (1, 1, 1, 1),
    },
    "admin": {
        "viewer": (0, 0, 0, 0), "uploader": (0, 0, 0, 0),
        "analyst": (0, 0, 0, 0), "admin": (1, 1, 1, 1),
    },
}


def seed_rbac(db: Session) -> None:
    roles_by_name = {}
    for name, description, rank in DEFAULT_ROLES:
        role = db.query(Role).filter(Role.name == name).first()
        if not role:
            role = Role(name=name, description=description, rank=rank, is_system=True)
            db.add(role)
            db.flush()
        roles_by_name[name] = role

    modules_by_key = {}
    for key, name, description in DEFAULT_MODULES:
        module = db.query(Module).filter(Module.key == key).first()
        if not module:
            module = Module(key=key, name=name, description=description, status="in_use")
            db.add(module)
            db.flush()
        else:
            if module.name != name:
                module.name = name
            if description and module.description != description:
                module.description = description
        modules_by_key[key] = module

    # Two passes: top-level items (incl. "Configuration") first, so their ids
    # exist when the second pass wires up children via parent_label.
    items_by_label = {}
    for label, url, module_key, sort_order, parent_label in DEFAULT_MENU_ITEMS:
        if parent_label is not None:
            continue
        module_id = modules_by_key[module_key].id if module_key else None
        existing_item = db.query(MenuItem).filter(MenuItem.url == url).first()
        if not existing_item:
            existing_item = MenuItem(
                label=label, url=url, module_id=module_id,
                sort_order=sort_order, is_active=True,
            )
            db.add(existing_item)
            db.flush()
        else:
            existing_item.label = label
            existing_item.module_id = module_id
            existing_item.sort_order = sort_order
            existing_item.parent_id = None
        items_by_label[label] = existing_item

    for label, url, module_key, sort_order, parent_label in DEFAULT_MENU_ITEMS:
        if parent_label is None:
            continue
        parent = items_by_label[parent_label]
        module_id = modules_by_key[module_key].id if module_key else None
        existing_item = db.query(MenuItem).filter(MenuItem.url == url).first()
        if not existing_item:
            db.add(MenuItem(
                label=label, url=url, module_id=module_id, parent_id=parent.id,
                sort_order=sort_order, is_active=True,
            ))
        else:
            # Fixup for rows seeded before this item had a parent group (e.g.
            # Image Library was top-level; admin pages were under a flat
            # "Admin" label) — re-parent + re-label known system nav rows.
            if existing_item.label in ("Admin", "Image Library") or existing_item.label != label:
                existing_item.label = label
            existing_item.module_id = module_id
            existing_item.parent_id = parent.id
            existing_item.sort_order = sort_order

    for module_key, role_perms in DEFAULT_PERMISSIONS.items():
        module = modules_by_key[module_key]
        for role_name, (view, create, edit, delete) in role_perms.items():
            role = roles_by_name[role_name]
            existing = (
                db.query(RolePermission)
                .filter(RolePermission.role_id == role.id, RolePermission.module_id == module.id)
                .first()
            )
            if not existing:
                db.add(RolePermission(
                    role_id=role.id, module_id=module.id,
                    can_view=bool(view), can_create=bool(create),
                    can_edit=bool(edit), can_delete=bool(delete),
                ))

    db.commit()

    unmigrated = db.query(User).filter(User.role_id.is_(None)).all()
    if unmigrated:
        for u in unmigrated:
            legacy = (u.role or "analyst").strip().lower()
            role = roles_by_name.get(legacy, roles_by_name["analyst"])
            u.role_id = role.id
        db.commit()
        logger.info("RBAC: backfilled role_id for %d user(s)", len(unmigrated))
