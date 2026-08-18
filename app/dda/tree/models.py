"""SQLAlchemy models for unlimited-depth tree library."""
from datetime import datetime, timezone

from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from ...database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class TreeNode(Base):
    __tablename__ = "dda_tree_nodes"

    id = Column(Integer, primary_key=True, index=True)
    parent_id = Column(Integer, ForeignKey("dda_tree_nodes.id"), nullable=True, index=True)
    node_name = Column(String(500), nullable=False)
    node_type = Column(String(100), default="Folder")
    node_level = Column(Integer, default=0)
    node_path = Column(Text, default="")  # display path: North Zone/Area A/2025
    slug = Column(String(64), nullable=False, index=True)
    physical_path = Column(Text, default="")  # relative slug path from storage root
    created_by = Column(String(100), default="")
    created_at = Column(DateTime, default=_utcnow)
    is_active = Column(Boolean, default=True, index=True)

    parent = relationship("TreeNode", remote_side=[id], backref="children")
    images = relationship("ImageLibrary", back_populates="node", cascade="all, delete-orphan")


class ImageLibrary(Base):
    __tablename__ = "dda_image_library"

    id = Column(Integer, primary_key=True, index=True)
    node_id = Column(Integer, ForeignKey("dda_tree_nodes.id"), nullable=False, index=True)
    image_name = Column(String(500), nullable=False)
    image_type = Column(String(100), default="GeoTIFF")
    # String, not Text: MySQL can't put a UNIQUE constraint on an unbounded
    # BLOB/TEXT column without an explicit key length. 500 matches the
    # sibling image_name column and is generous for a storage-root-relative
    # path. Works unchanged on SQLite/PostgreSQL too.
    file_path = Column(String(500), nullable=False, unique=True)  # relative to storage root
    capture_date = Column(DateTime, nullable=True)
    uploaded_by = Column(String(100), default="")
    uploaded_on = Column(DateTime, default=_utcnow)
    # BigInteger, not Integer: this project's GeoTIFFs regularly exceed 2GB
    # (plain 32-bit INTEGER's max), e.g. a 3.9GB file seen during testing.
    # SQLite silently tolerated the overflow (no real width enforcement);
    # MySQL and PostgreSQL both reject it, correctly.
    file_size_bytes = Column(BigInteger, default=0)
    thumb_cache_key = Column(String(64), default="")
    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    has_georef = Column(Boolean, default=False)
    bounds_json = Column(Text, default="")
    format = Column(String(32), default="")

    node = relationship("TreeNode", back_populates="images")


class AuditLog(Base):
    __tablename__ = "dda_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    action = Column(String(100), nullable=False)
    node_id = Column(Integer, ForeignKey("dda_tree_nodes.id"), nullable=True, index=True)
    old_value = Column(Text, default="")
    new_value = Column(Text, default="")
    action_date = Column(DateTime, default=_utcnow)
    action_by = Column(String(100), default="")
