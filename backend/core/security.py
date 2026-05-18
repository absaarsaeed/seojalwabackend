"""JWT, password hashing, admin session helpers."""
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt


# ---------- Password hashing ----------
def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------- JWT ----------
def _parse_expires(value: str) -> timedelta:
    """Parse strings like '15m', '7d', '2h' into timedelta."""
    if not value:
        return timedelta(minutes=15)
    unit = value[-1].lower()
    try:
        n = int(value[:-1])
    except ValueError:
        return timedelta(minutes=15)
    if unit == "m":
        return timedelta(minutes=n)
    if unit == "h":
        return timedelta(hours=n)
    if unit == "d":
        return timedelta(days=n)
    if unit == "s":
        return timedelta(seconds=n)
    return timedelta(minutes=15)


def create_access_token(user_id: str) -> str:
    secret = os.environ["JWT_SECRET"]
    delta = _parse_expires(os.environ.get("JWT_EXPIRES_IN", "15m"))
    payload = {
        "sub": user_id,
        "type": "access",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + delta,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def create_refresh_token(user_id: str) -> str:
    secret = os.environ["JWT_REFRESH_SECRET"]
    delta = _parse_expires(os.environ.get("JWT_REFRESH_EXPIRES_IN", "7d"))
    payload = {
        "sub": user_id,
        "type": "refresh",
        "jti": str(uuid.uuid4()),
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + delta,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict[str, Any]:
    secret = os.environ["JWT_SECRET"]
    return jwt.decode(token, secret, algorithms=["HS256"])


def decode_refresh_token(token: str) -> dict[str, Any]:
    secret = os.environ["JWT_REFRESH_SECRET"]
    return jwt.decode(token, secret, algorithms=["HS256"])


# ---------- Admin session ----------
def create_admin_session_token() -> str:
    return str(uuid.uuid4()).replace("-", "") + str(uuid.uuid4()).replace("-", "")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
