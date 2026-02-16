import os
from datetime import datetime, timedelta
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from .database import get_db
from .models import User

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-fallback-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 days
COOKIE_NAME = "satellite_token"

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: int) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def get_user_from_token(token: str, db: Session) -> Optional[User]:
    """Resolve user from JWT token (used as fallback when header/cookie not sent)."""
    if not token:
        print("[AUTH] get_user_from_token: token is empty/None")
        return None
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id_str = payload.get("sub")
        print(f"[AUTH] decoded token OK, sub={user_id_str}")
        if user_id_str is None:
            return None
        try:
            user_id = int(user_id_str)
        except (ValueError, TypeError):
            return None
    except JWTError as e:
        print(f"[AUTH] JWT decode FAILED: {e}")
        return None
    user = get_user_by_id(db, user_id)
    print(f"[AUTH] DB lookup: user={'found' if user else 'NOT FOUND'}")
    return user


def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
) -> Optional[User]:
    print(f"[AUTH] get_current_user called")
    print(f"[AUTH]   credentials present: {credentials is not None}")
    print(f"[AUTH]   cookie present: {request.cookies.get(COOKIE_NAME) is not None}")
    print(f"[AUTH]   Authorization header: {request.headers.get('authorization', 'MISSING')[:50]}")
    # 1) Try Bearer header
    if credentials:
        user = get_user_from_token(credentials.credentials, db)
        if user:
            return user
    # 2) Try cookie (sent automatically by browser on same-origin requests)
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return get_user_from_token(token, db)
    print("[AUTH] No valid auth found, returning None")
    return None
