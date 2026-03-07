from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    created_at = Column(DateTime, default=datetime.utcnow)

    detections = relationship("DetectionRun", back_populates="user", order_by="desc(DetectionRun.created_at)")


class DetectionRun(Base):
    __tablename__ = "detection_runs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(255), default="Untitled run")
    method = Column(String(64), nullable=False)
    total_pixels = Column(Integer, nullable=False)
    changed_pixels = Column(Integer, nullable=False)
    change_percentage = Column(Float, nullable=False)
    regions_count = Column(Integer, default=0)
    overlay_path = Column(String(512), default="")
    before_thumb_path = Column(String(512), default="")
    after_thumb_path = Column(String(512), default="")
    zone = Column(String(128), default="")
    village = Column(String(128), default="")
    regions_json = Column(Text, default="[]")  # JSON list of regions
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="detections")
