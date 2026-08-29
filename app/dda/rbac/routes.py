"""FastAPI routes for the dynamic RBAC admin system: roles, per-role module
permissions, modules, menu items, and admin-driven user management."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...auth import get_password_hash, get_user_by_email
from ...database import get_db
from ...models import User
from ..dda_auth import current_dda_user
from .models import MenuItem, Module, Role, RolePermission
from .permissions import require_module_permission, user_can

router = APIRouter()


def _role_dict(role: Role) -> dict:
    return {
        "id": role.id, "name": role.name, "description": role.description,
        "rank": role.rank, "isSystem": role.is_system,
        "isActive": role.is_active if role.is_active is not None else True,
    }


def _module_dict(module: Module) -> dict:
    return {
        "id": module.id, "key": module.key, "name": module.name,
        "description": module.description, "status": module.status,
    }


def _menu_item_dict(item: MenuItem) -> dict:
    return {
        "id": item.id, "label": item.label, "url": item.url, "moduleId": item.module_id,
        "parentId": item.parent_id, "icon": item.icon or "", "sortOrder": item.sort_order,
        "isActive": item.is_active,
    }


def _user_dict(u: User, role_name: str, role_id: Optional[int]) -> dict:
    return {
        "id": u.id, "email": u.email, "fullName": u.full_name,
        "role": role_name, "roleId": role_id,
    }


# -------------------------------------------------------- Current user ----

@router.get("/rbac/me/permissions")
def my_permissions(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """What the logged-in user's role can do, per module — drives dynamic
    show/hide/disable in the UI (Upload, Run detection, Delete, etc.) so
    the frontend always matches whatever's set in the Roles & Users
    permission matrix, without hardcoding any role name."""
    modules = db.query(Module).all()
    return {
        m.key: {
            "view": user_can(user, db, m.key, "view"),
            "create": user_can(user, db, m.key, "create"),
            "edit": user_can(user, db, m.key, "edit"),
            "delete": user_can(user, db, m.key, "delete"),
        }
        for m in modules
    }


# ---------------------------------------------------------------- Roles ----

class RoleBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = ""
    rank: int = 0
    isActive: bool = True


@router.get("/rbac/roles")
def list_roles(
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "view")),
):
    roles = db.query(Role).order_by(Role.rank).all()
    return {"roles": [_role_dict(r) for r in roles]}


@router.post("/rbac/roles")
def create_role(
    body: RoleBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "create")),
):
    name = body.name.strip().lower()
    if db.query(Role).filter(Role.name == name).first():
        raise HTTPException(status_code=409, detail="A role with this name already exists.")
    role = Role(name=name, description=body.description, rank=body.rank, is_system=False, is_active=body.isActive)
    db.add(role)
    db.commit()
    db.refresh(role)
    return _role_dict(role)


@router.put("/rbac/roles/{role_id}")
def update_role(
    role_id: int,
    body: RoleBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    role.description = body.description
    role.rank = body.rank
    role.is_active = body.isActive
    if not role.is_system:
        role.name = body.name.strip().lower()
    db.commit()
    return _role_dict(role)


# ------------------------------------------------- Role permission matrix --

class PermissionEntry(BaseModel):
    moduleId: int
    canView: bool = False
    canCreate: bool = False
    canEdit: bool = False
    canDelete: bool = False


class PermissionMatrixBody(BaseModel):
    permissions: List[PermissionEntry]


@router.get("/rbac/roles/{role_id}/permissions")
def get_role_permissions(
    role_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "view")),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    modules = db.query(Module).order_by(Module.name).all()
    perms_by_module = {
        p.module_id: p for p in db.query(RolePermission).filter(RolePermission.role_id == role_id).all()
    }
    permissions = []
    for m in modules:
        p = perms_by_module.get(m.id)
        permissions.append({
            "moduleId": m.id, "moduleKey": m.key, "moduleName": m.name,
            "canView": bool(p and p.can_view), "canCreate": bool(p and p.can_create),
            "canEdit": bool(p and p.can_edit), "canDelete": bool(p and p.can_delete),
        })
    return {"role": _role_dict(role), "permissions": permissions}


@router.put("/rbac/roles/{role_id}/permissions")
def set_role_permissions(
    role_id: int,
    body: PermissionMatrixBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    role = db.query(Role).filter(Role.id == role_id).first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    for entry in body.permissions:
        perm = (
            db.query(RolePermission)
            .filter(RolePermission.role_id == role_id, RolePermission.module_id == entry.moduleId)
            .first()
        )
        if not perm:
            perm = RolePermission(role_id=role_id, module_id=entry.moduleId)
            db.add(perm)
        perm.can_view = entry.canView
        perm.can_create = entry.canCreate
        perm.can_edit = entry.canEdit
        perm.can_delete = entry.canDelete
    db.commit()
    return {"ok": True}


# -------------------------------------------------------------- Modules ----

class ModuleBody(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    description: str = ""
    status: str = "in_use"


@router.get("/rbac/modules")
def list_modules(
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "view")),
):
    modules = db.query(Module).order_by(Module.name).all()
    return {"modules": [_module_dict(m) for m in modules]}


@router.post("/rbac/modules")
def create_module(
    body: ModuleBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "create")),
):
    key = body.key.strip().lower().replace(" ", "_")
    if db.query(Module).filter(Module.key == key).first():
        raise HTTPException(status_code=409, detail="A module with this key already exists.")
    module = Module(key=key, name=body.name, description=body.description, status=body.status)
    db.add(module)
    db.commit()
    db.refresh(module)
    return _module_dict(module)


@router.put("/rbac/modules/{module_id}")
def update_module(
    module_id: int,
    body: ModuleBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found.")
    module.name = body.name
    module.description = body.description
    module.status = body.status
    db.commit()
    return _module_dict(module)


@router.delete("/rbac/modules/{module_id}")
def delete_module(
    module_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "delete")),
):
    module = db.query(Module).filter(Module.id == module_id).first()
    if not module:
        raise HTTPException(status_code=404, detail="Module not found.")
    if db.query(MenuItem).filter(MenuItem.module_id == module_id).first():
        raise HTTPException(status_code=400, detail="Cannot delete a module that is still linked to a menu item.")
    db.query(RolePermission).filter(RolePermission.module_id == module_id).delete()
    db.delete(module)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------ Menu items ---

class MenuItemBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=128)
    url: str = Field(..., min_length=1, max_length=255)
    moduleId: Optional[int] = None
    parentId: Optional[int] = None
    icon: str = Field(default="", max_length=64)
    isActive: bool = True


class ReorderBody(BaseModel):
    orderedIds: List[int]


@router.get("/rbac/menu-items")
def list_menu_items(
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "view")),
):
    items = db.query(MenuItem).order_by(MenuItem.sort_order).all()
    return {"menuItems": [_menu_item_dict(i) for i in items]}


def _validate_parent(db: Session, parent_id: Optional[int], self_id: Optional[int] = None) -> None:
    if parent_id is None:
        return
    if parent_id == self_id:
        raise HTTPException(status_code=400, detail="A menu item cannot be its own parent.")
    parent = db.query(MenuItem).filter(MenuItem.id == parent_id).first()
    if not parent:
        raise HTTPException(status_code=400, detail="Unknown parent menu item.")
    if parent.parent_id is not None:
        raise HTTPException(status_code=400, detail="Only one level of sub-menus is supported — pick a top-level item as the parent.")


@router.post("/rbac/menu-items")
def create_menu_item(
    body: MenuItemBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "create")),
):
    _validate_parent(db, body.parentId)
    next_order = db.query(MenuItem).filter(MenuItem.parent_id == body.parentId).count()
    item = MenuItem(
        label=body.label, url=body.url, module_id=body.moduleId, parent_id=body.parentId,
        icon=body.icon, sort_order=next_order, is_active=body.isActive,
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return _menu_item_dict(item)


@router.put("/rbac/menu-items/{item_id}")
def update_menu_item(
    item_id: int,
    body: MenuItemBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found.")
    _validate_parent(db, body.parentId, self_id=item_id)
    if body.parentId != item.parent_id:
        item.sort_order = db.query(MenuItem).filter(MenuItem.parent_id == body.parentId).count()
    item.parent_id = body.parentId
    item.icon = body.icon
    item.label = body.label
    item.url = body.url
    item.module_id = body.moduleId
    item.is_active = body.isActive
    db.commit()
    return _menu_item_dict(item)


@router.delete("/rbac/menu-items/{item_id}")
def delete_menu_item(
    item_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "delete")),
):
    item = db.query(MenuItem).filter(MenuItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Menu item not found.")
    if db.query(MenuItem).filter(MenuItem.parent_id == item_id).first():
        raise HTTPException(status_code=400, detail="Delete or move its sub-items first.")
    db.delete(item)
    db.commit()
    return {"ok": True}


@router.post("/rbac/menu-items/reorder")
def reorder_menu_items(
    body: ReorderBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    for index, item_id in enumerate(body.orderedIds):
        db.query(MenuItem).filter(MenuItem.id == item_id).update({"sort_order": index})
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ Users --

class UserCreateBody(BaseModel):
    email: EmailStr
    fullName: str = ""
    password: str = Field(..., min_length=8)
    roleId: int


class UserUpdateBody(BaseModel):
    fullName: str = ""
    roleId: int
    password: Optional[str] = Field(default=None, min_length=8)


@router.get("/rbac/users")
def list_users(
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "view")),
):
    roles_by_id = {r.id: r.name for r in db.query(Role).all()}
    users = db.query(User).order_by(User.id).all()
    return {"users": [_user_dict(u, roles_by_id.get(u.role_id, u.role or ""), u.role_id) for u in users]}


@router.post("/rbac/users")
def create_user(
    body: UserCreateBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "create")),
):
    if get_user_by_email(db, str(body.email)):
        raise HTTPException(status_code=409, detail="A user with this email already exists.")
    role = db.query(Role).filter(Role.id == body.roleId).first()
    if not role:
        raise HTTPException(status_code=400, detail="Unknown role.")
    new_user = User(
        email=str(body.email), full_name=body.fullName,
        hashed_password=get_password_hash(body.password),
        role_id=role.id, role=role.name,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return _user_dict(new_user, role.name, role.id)


@router.put("/rbac/users/{user_id}")
def update_user(
    user_id: int,
    body: UserUpdateBody,
    db: Session = Depends(get_db),
    _user: User = Depends(require_module_permission("admin", "edit")),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    role = db.query(Role).filter(Role.id == body.roleId).first()
    if not role:
        raise HTTPException(status_code=400, detail="Unknown role.")
    target.full_name = body.fullName
    target.role_id = role.id
    target.role = role.name
    if body.password:
        target.hashed_password = get_password_hash(body.password)
    db.commit()
    return _user_dict(target, role.name, role.id)


@router.delete("/rbac/users/{user_id}")
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_module_permission("admin", "delete")),
):
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account.")
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")
    db.delete(target)
    try:
        db.commit()
    except IntegrityError:
        # login_history / detection_runs both FK to users.id with no cascade —
        # by design, so login/audit trails and past detection runs survive a
        # user being removed. Surface a clear reason instead of a raw 500.
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail="Cannot delete this user — they have login history or detection "
                   "runs on record. Reassign them to a different role instead if you "
                   "want to revoke their access.",
        )
    return {"ok": True}
