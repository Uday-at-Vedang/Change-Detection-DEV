import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User, LoginHistory, PasswordResetToken, LoginOtp

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
    """Case-insensitive email lookup with whitespace stripped.

    Emails are supposed to be stored lowercase (see register()), but a) users
    typing them in login forms may leave stray whitespace or capitalisation,
    and b) older accounts predating the .lower() normalisation may still be
    mixed-case. Using LOWER()/TRIM() on both sides avoids the case where the
    same email is treated as "already exists" by one route and "not found" by
    another.
    """
    if not email:
        return None
    from sqlalchemy import func
    normalised = email.strip().lower()
    return (
        db.query(User)
        .filter(func.lower(func.trim(User.email)) == normalised)
        .first()
    )


def get_user_by_phone(db: Session, phone: str) -> Optional[User]:
    if not phone:
        return None
    return db.query(User).filter(User.phone == phone).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def normalize_phone(raw: str, country_code: Optional[str] = None) -> Optional[str]:
    """Return the number as E.164 ("+CCXXXXXXXX"), or None if invalid.

    Accepts either a raw string that already starts with '+', or a local
    number plus a separate country dial code. India-specific rules (mobile
    prefix must be 6-9, exactly 10 digits) are applied when the country
    dial code resolves to '+91'; every other country is checked only for
    total length (7–15 digits per the E.164 spec).
    """
    raw = (raw or "").strip()
    cc = (country_code or "").strip()
    if raw.startswith("+"):
        digits = re.sub(r"\D", "", raw)
        e164_country = ""
        if not cc:
            for dial in ("91", "1", "44", "61", "971", "65", "880", "94", "977", "92"):
                if digits.startswith(dial):
                    e164_country = "+" + dial
                    break
        else:
            e164_country = "+" + re.sub(r"\D", "", cc)
            if not digits.startswith(e164_country[1:]):
                return None
        local = digits[len(e164_country) - 1:] if e164_country else digits
    else:
        if not cc:
            cc = "+91"
        e164_country = "+" + re.sub(r"\D", "", cc)
        local = re.sub(r"\D", "", raw)
        if local.startswith(e164_country[1:]) and len(local) > 10:
            local = local[len(e164_country) - 1:]
    if not e164_country.startswith("+") or len(e164_country) < 2:
        return None
    if not local.isdigit():
        return None
    total = e164_country[1:] + local
    if not (7 <= len(total) <= 15):
        return None
    if e164_country == "+91" and (len(local) != 10 or local[0] not in "6789"):
        return None
    return e164_country + local


def mask_phone(phone: str) -> str:
    """Mask an E.164 number for display: '+91******3210'."""
    p = (phone or "").strip()
    if not p:
        return "******"
    if p.startswith("+"):
        digits = re.sub(r"\D", "", p)
        if len(digits) <= 4:
            return "+" + digits
        for dial in ("91", "1", "44", "61", "971", "65", "880", "94", "977", "92"):
            if digits.startswith(dial) and len(digits) > len(dial) + 3:
                local = digits[len(dial):]
                return f"+{dial}{'*' * max(0, len(local) - 4)}{local[-4:]}"
        return "+" + "*" * max(0, len(digits) - 4) + digits[-4:]
    digits = re.sub(r"\D", "", p)
    if len(digits) < 4:
        return "******"
    return f"******{digits[-4:]}"


def get_user_from_token(token: str, db: Session, *, allow_pending_otp: bool = False) -> Optional[User]:
    """Resolve user from JWT token.

    Tokens issued after password-check, before SMS OTP, carry
    purpose=otp_pending and must not grant a session by themselves.
    """
    if not token:
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        purpose = payload.get("purpose") or "session"
        if purpose == "otp_pending" and not allow_pending_otp:
            return None
        if purpose not in ("session", "otp_pending"):
            return None
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


LOGIN_OTP_EXPIRE_MINUTES = 10
LOGIN_OTP_MAX_ATTEMPTS = 5
LOGIN_OTP_RESEND_SECONDS = 45


def _hash_otp(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def mask_email(email: str) -> str:
    local, _, domain = (email or "").partition("@")
    if not domain:
        return "***"
    shown = (local[:1] + "***") if len(local) <= 2 else (local[:2] + "***")
    return f"{shown}@{domain}"


def create_login_otp(db: Session, user: User) -> Tuple[LoginOtp, str]:
    """Issue a fresh 6-digit email OTP, invalidating unused ones for this user."""
    db.query(LoginOtp).filter(
        LoginOtp.user_id == user.id,
        LoginOtp.used_at.is_(None),
    ).delete(synchronize_session=False)
    raw = f"{secrets.randbelow(1_000_000):06d}"
    row = LoginOtp(
        user_id=user.id,
        code_hash=_hash_otp(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=LOGIN_OTP_EXPIRE_MINUTES),
        attempts=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row, raw


def latest_unused_otp(db: Session, user_id: int) -> Optional[LoginOtp]:
    return (
        db.query(LoginOtp)
        .filter(LoginOtp.user_id == user_id, LoginOtp.used_at.is_(None))
        .order_by(LoginOtp.id.desc())
        .first()
    )


def verify_login_otp(db: Session, user: User, otp_id: int, code: str) -> str:
    """Return '' if valid; otherwise a short error token for the caller to map."""
    cleaned = (code or "").strip().replace(" ", "")
    now = datetime.now(timezone.utc)
    row = (
        db.query(LoginOtp)
        .filter(
            LoginOtp.id == otp_id,
            LoginOtp.user_id == user.id,
            LoginOtp.used_at.is_(None),
        )
        .first()
    )
    if not row:
        return "expired"
    exp = row.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    if exp < now:
        return "expired"
    if (row.attempts or 0) >= LOGIN_OTP_MAX_ATTEMPTS:
        return "locked"
    if not cleaned.isdigit() or len(cleaned) != 6 or row.code_hash != _hash_otp(cleaned):
        row.attempts = (row.attempts or 0) + 1
        db.commit()
        return "invalid"
    row.used_at = now
    db.commit()
    return ""


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
