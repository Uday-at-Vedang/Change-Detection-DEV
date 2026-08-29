from datetime import datetime, timezone
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float
from sqlalchemy.orm import relationship

from .database import Base


def _utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    full_name = Column(String(255), default="")
    role = Column(String(32), default="analyst")
    # FK into the dynamic RBAC role table (app/dda/rbac/models.py). Nullable +
    # the legacy `role` string column both stay: existing role-rank checks in
    # dda_auth.py fall back to matching `role` by name for any user not yet
    # backfilled (see app/dda/rbac/seed.py).
    role_id = Column(Integer, ForeignKey("dda_roles.id"), nullable=True, index=True)
    totp_secret = Column(String(64), nullable=True)
    totp_enabled = Column(Integer, default=0)  # 0/1 — SQLite-friendly boolean
    totp_confirmed_at = Column(DateTime, nullable=True)
    # 10-digit Indian mobile used for login SMS OTP. Nullable so existing
    # accounts can bind a number on first successful OTP verify.
    phone = Column(String(20), unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=_utcnow)

    detections = relationship("DetectionRun", back_populates="user", order_by="desc(DetectionRun.created_at)")
    role_obj = relationship("Role", foreign_keys=[role_id])


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
    before_full_path = Column(String(512), default="")
    before_thumb_path = Column(String(512), default="")
    after_thumb_path = Column(String(512), default="")
    after_full_path = Column(String(512), default="")
    zone = Column(String(128), default="")
    village = Column(String(128), default="")
    regions_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=_utcnow)

    user = relationship("User", back_populates="detections")


class PasswordResetToken(Base):
    """One-time, expiring token backing the Forgot Password flow. The raw
    token is only ever emailed to the user — this table stores its SHA-256
    hash, same principle as never storing plaintext passwords."""
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=_utcnow)


class LoginHistory(Base):
    __tablename__ = "login_history"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable: a failed login against an unknown email has no user row to point at.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    # Always populated (even for unknown emails) — needed for account-lockout queries
    # that can't rely on user_id.
    attempted_email = Column(String(255), nullable=False, index=True)
    # login_success | login_failed | login_blocked | logout
    event_type = Column(String(20), nullable=False, index=True)
    # bad_password | unknown_email | account_locked | ip_throttled — internal only,
    # never returned to the client (same "don't leak why" precedent as reset_password).
    failure_reason = Column(String(30), nullable=True)
    ip_address = Column(String(45), default="")  # 45 = max IPv6 literal length
    user_agent = Column(String(512), default="")
    created_at = Column(DateTime, default=_utcnow, index=True)


class LoginOtp(Base):
    """One-time email code issued after a successful password check at login."""
    __tablename__ = "login_otps"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    code_hash = Column(String(64), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, default=0)
    created_at = Column(DateTime, default=_utcnow, index=True)
