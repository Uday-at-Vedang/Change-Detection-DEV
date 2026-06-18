import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from ..database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class DdaZone(Base):
    __tablename__ = "dda_zones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), unique=True, nullable=False, index=True)
    slug = Column(String(64), unique=True, nullable=True, index=True)
    mode = Column(String(32), default="admin")  # admin | grid_parent
    created_at = Column(DateTime, default=_utcnow)

    villages = relationship("DdaVillage", back_populates="zone", cascade="all, delete-orphan")


class DdaVillage(Base):
    """Folder under a zone (exposed as folder in API/UI)."""
    __tablename__ = "dda_villages"

    id = Column(Integer, primary_key=True, index=True)
    zone_id = Column(Integer, ForeignKey("dda_zones.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False, index=True)
    slug = Column(String(64), nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow)

    zone = relationship("DdaZone", back_populates="villages")
    images = relationship("ImageAsset", back_populates="village")


class DdaLocalFileIndex(Base):
    """Maps library-relative paths to zone/folder/year for traceability."""
    __tablename__ = "dda_local_file_index"

    id = Column(Integer, primary_key=True, index=True)
    relative_path = Column(String(512), unique=True, nullable=False, index=True)
    zone_id = Column(Integer, ForeignKey("dda_zones.id"), nullable=True, index=True)
    folder_id = Column(Integer, ForeignKey("dda_villages.id"), nullable=True, index=True)
    year = Column(Integer, nullable=True, index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    zone = relationship("DdaZone")
    folder = relationship("DdaVillage")


class ImageAsset(Base):
    """Centralized satellite / drone image (FR-01, FR-02)."""
    __tablename__ = "dda_image_assets"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(String(36), unique=True, nullable=False, default=lambda: str(uuid.uuid4()), index=True)
    zone_id = Column(Integer, ForeignKey("dda_zones.id"), nullable=True, index=True)
    village_id = Column(Integer, ForeignKey("dda_villages.id"), nullable=True, index=True)
    area_name = Column(String(128), default="")
    year = Column(Integer, nullable=True, index=True)
    grid_id = Column(String(64), default="", index=True)

    capture_date = Column(Date, nullable=True)
    source = Column(String(32), default="satellite")  # satellite | drone
    format = Column(String(32), default="geotiff")
    original_filename = Column(String(255), default="")

    file_path = Column(String(512), nullable=False)
    thumb_path = Column(String(512), default="")
    preview_path = Column(String(512), default="")

    crs = Column(String(64), default="")
    bounds_json = Column(Text, default="")  # WGS84 [west, south, east, north]
    has_georef = Column(Boolean, default=False)
    manual_location_json = Column(Text, default="")

    width = Column(Integer, default=0)
    height = Column(Integer, default=0)
    file_size_bytes = Column(Integer, default=0)

    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=_utcnow)

    zone = relationship("DdaZone")
    village = relationship("DdaVillage", back_populates="images")


class DetectionJob(Base):
    """Async change detection job (FR-04 skeleton)."""
    __tablename__ = "dda_detection_jobs"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(32), default="queued", index=True)  # queued|running|completed|failed
    base_image_id = Column(Integer, ForeignKey("dda_image_assets.id"), nullable=True)
    comparison_image_id = Column(Integer, ForeignKey("dda_image_assets.id"), nullable=True)
    method = Column(String(64), default="AI-Based Deep Learning")
    params_json = Column(Text, default="{}")
    run_id = Column(Integer, ForeignKey("detection_runs.id"), nullable=True)
    error_message = Column(Text, default="")
    notify_email = Column(String(255), default="")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class RegionReview(Base):
    """Per-region human review state (FR-08)."""
    __tablename__ = "dda_region_reviews"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(Integer, ForeignKey("detection_runs.id"), nullable=False, index=True)
    region_id = Column(Integer, nullable=False, index=True)
    status = Column(String(32), default="pending")  # pending|confirmed|false_positive|submitted
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    notes = Column(Text, default="")
    reviewed_at = Column(DateTime, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
