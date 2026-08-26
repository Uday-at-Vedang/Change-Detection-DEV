"""SQLAlchemy models for the dynamic RBAC system: roles, modules, per-role
module permissions, and the DB-backed nav menu."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import relationship

from ...database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class Role(Base):
    __tablename__ = "dda_roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(64), nullable=False, unique=True, index=True)
    description = Column(String(255), default="")
    rank = Column(Integer, default=0)
    # Seeded roles (viewer/uploader/analyst/admin) — protected from deletion
    # so the built-in permission gates in dda_auth.py always have a role to
    # resolve against.
    is_system = Column(Boolean, default=False)
    created_at = Column(DateTime, default=_utcnow)


class Module(Base):
    __tablename__ = "dda_modules"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(128), nullable=False)
    description = Column(String(255), default="")
    status = Column(String(20), default="in_use")  # in_use | deprecated
    created_at = Column(DateTime, default=_utcnow)


class RolePermission(Base):
    __tablename__ = "dda_role_permissions"
    __table_args__ = (UniqueConstraint("role_id", "module_id", name="uq_role_module"),)

    id = Column(Integer, primary_key=True, index=True)
    role_id = Column(Integer, ForeignKey("dda_roles.id"), nullable=False, index=True)
    module_id = Column(Integer, ForeignKey("dda_modules.id"), nullable=False, index=True)
    can_view = Column(Boolean, default=False)
    can_create = Column(Boolean, default=False)
    can_edit = Column(Boolean, default=False)
    can_delete = Column(Boolean, default=False)

    role = relationship("Role")
    module = relationship("Module")


class MenuItem(Base):
    __tablename__ = "dda_menu_items"

    id = Column(Integer, primary_key=True, index=True)
    label = Column(String(128), nullable=False)
    url = Column(String(255), nullable=False)
    module_id = Column(Integer, ForeignKey("dda_modules.id"), nullable=True, index=True)
    # Self-referential parent for one level of nesting — unused today (this
    # app's nav is flat) but keeps the model ready without a later migration.
    parent_id = Column(Integer, ForeignKey("dda_menu_items.id"), nullable=True)
    sort_order = Column(Integer, default=0)
    icon = Column(String(64), default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    module = relationship("Module")
    parent = relationship("MenuItem", remote_side=[id], backref="children")
