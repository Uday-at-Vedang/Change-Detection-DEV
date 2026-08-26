import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, LoginHistory, PasswordResetToken

logger = logging.getLogger(__name__)

_env_secret = os.environ.get("SECRET_KEY", "").strip()
if _env_secret:
    SECRET_KEY = _env_secret
else:
    # Never fall back to a fixed/shared string here — a hardcoded value in
    # source is visible to anyone with the repo, and could be used to forge a
    # valid login cookie for any user id without ever knowing a password.
    # A fresh random secret per process closes that hole; the trade-off is
    # every existing session is invalidated on each restart until a
    # persistent SECRET_KEY is set in the environment (see .env.example).
    SECRET_KEY = secrets.token_hex(32)
    logger.warning(
        "SECRET_KEY env var not set — generated a random one-time secret for "
        "this process (existing sessions will be invalidated on restart). "
        "Set SECRET_KEY to a persistent random string in your environment, "
        "especially in production — see .env.example."
    )
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
COOKIE_NAME = "satellite_token"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)

# Brute-force lockout thresholds. IP-based is the primary defense (an attacker
# can't lock out a legitimate user just by failing their password from
# elsewhere); account-based is secondary. Both env-configurable.
LOGIN_IP_MAX_FAILURES = int(os.environ.get("LOGIN_IP_MAX_FAILURES", "20"))
LOGIN_IP_WINDOW_MINUTES = int(os.environ.get("LOGIN_IP_WINDOW_MINUTES", "15"))
LOGIN_ACCOUNT_MAX_FAILURES = int(os.environ.get("LOGIN_ACCOUNT_MAX_FAILURES", "5"))
LOGIN_ACCOUNT_WINDOW_MINUTES = int(os.environ.get("LOGIN_ACCOUNT_WINDOW_MINUTES", "15"))

_COOKIE_SECURE_ENV = os.environ.get("COOKIE_SECURE", "").strip().lower()
if _COOKIE_SECURE_ENV in ("1", "true", "yes"):
    IS_SECURE_COOKIE = True
elif _COOKIE_SECURE_ENV in ("0", "false", "no"):
    IS_SECURE_COOKIE = False
else:
    # Render sets RENDER=true on its platform (genuine HTTPS hosting).
    # Deliberately NOT keying off SPACE_ID: this project's own .env sets
    # SPACE_ID=local-dev/satdetect-dev for *local* HTTP dev (to force
    # "hosted" UI behavior, unrelated to HTTPS), so treating any SPACE_ID as
    # "we're on HTTPS" would wrongly mark local dev secure and silently drop
    # the auth cookie (verified via smoke test — this was the actual bug).
    # A genuine Hugging Face Spaces deployment should set COOKIE_SECURE=true
    # explicitly via the branch above.
    IS_SECURE_COOKIE = bool(os.environ.get("RENDER"))


def get_client_ip(request: Request) -> str:
    """Real client IP behind Render's proxy (no --proxy-headers on uvicorn, so
    request.client.host would otherwise report the proxy's IP, not the visitor's)."""
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def get_user_agent(request: Request) -> str:
    return request.headers.get("user-agent", "")[:512]


def check_ip_throttle(db: Session, ip: str) -> bool:
    """True if this IP has too many recent failed login attempts."""
    if not ip:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_IP_WINDOW_MINUTES)
    count = (
        db.query(LoginHistory)
        .filter(
            LoginHistory.ip_address == ip,
            LoginHistory.event_type == "login_failed",
            LoginHistory.created_at >= cutoff,
        )
        .count()
    )
    return count >= LOGIN_IP_MAX_FAILURES


def check_account_lockout(db: Session, email: str) -> bool:
    """True if this email has too many recent failed login attempts."""
    if not email:
        return False
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=LOGIN_ACCOUNT_WINDOW_MINUTES)
    count = (
        db.query(LoginHistory)
        .filter(
            LoginHistory.attempted_email == email,
            LoginHistory.event_type == "login_failed",
            LoginHistory.created_at >= cutoff,
        )
        .count()
    )
    return count >= LOGIN_ACCOUNT_MAX_FAILURES


def record_login_event(
    db: Session,
    *,
    event_type: str,
    attempted_email: str,
    user_id: Optional[int] = None,
    failure_reason: Optional[str] = None,
    ip_address: str = "",
    user_agent: str = "",
) -> None:
    entry = LoginHistory(
        user_id=user_id,
        attempted_email=attempted_email,
        event_type=event_type,
        failure_reason=failure_reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(entry)
    db.commit()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


PASSWORD_RESET_TOKEN_EXPIRE_MINUTES = 30


def _hash_reset_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


def create_password_reset_token(db: Session, user: User) -> str:
    """Issue a fresh one-time reset token for `user`, invalidating any
    previous unused ones (so an old, possibly-leaked link can't still work
    once a new reset was requested). Returns the raw token — only the caller
    (the emailed link) ever sees it; the DB only keeps its hash."""
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).delete(synchronize_session=False)

    raw_token = secrets.token_urlsafe(32)
    db.add(PasswordResetToken(
        user_id=user.id,
        token_hash=_hash_reset_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_TOKEN_EXPIRE_MINUTES),
    ))
    db.commit()
    return raw_token


def consume_password_reset_token(db: Session, raw_token: str) -> Optional[User]:
    """Validate + burn a reset token in one step (single use). The expiry
    comparison is done in the SQL filter, not in Python, so it's never
    exposed to naive/aware datetime mismatches from what the DB driver hands
    back for a stored DATETIME column."""
    if not raw_token:
        return None
    now = datetime.now(timezone.utc)
    entry = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == _hash_reset_token(raw_token),
            PasswordResetToken.used_at.is_(None),
            PasswordResetToken.expires_at >= now,
        )
        .first()
    )
    if not entry:
        return None
    entry.used_at = now
    db.commit()
    return get_user_by_id(db, entry.user_id)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_from_token(token: str, db: Session) -> Optional[User]:
    """Resolve user from JWT token."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        if user_id_str is None:
            return None
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            logger.warning("JWT 'sub' claim is not a valid integer")
            return None
    except JWTError:
        return None
    return get_user_by_id(db, user_id)


def get_or_create_guest_user(db: Session) -> User:
    """Shared anonymous account when login is disabled."""
    guest_email = "__guest__@system.local"
    user = get_user_by_email(db, guest_email)
    if user:
        return user
    user = User(
        email=guest_email,
        hashed_password=get_password_hash("guest-not-used"),
        full_name="Guest",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    if credentials:
        user = get_user_from_token(credentials.credentials, db)
        if user:
            return user
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return get_user_from_token(token, db)
    return None
