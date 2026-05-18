"""FastAPI dependency injection helpers for auth."""
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, Header, Cookie, Request

from core.database import get_db
from core.response import APIError
from core.security import decode_access_token


async def get_current_user(
    authorization: Optional[str] = Header(None),
) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise APIError("Missing or invalid Authorization header",
                       code="UNAUTHORIZED", status_code=401)
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_access_token(token)
    except Exception as e:
        raise APIError(f"Invalid token: {e}", code="UNAUTHORIZED",
                       status_code=401)
    user_id = payload.get("sub")
    if not user_id:
        raise APIError("Invalid token payload", code="UNAUTHORIZED",
                       status_code=401)
    db = get_db()
    user = await db.users.find_one({"id": user_id, "deleted": {"$ne": True}},
                                   {"_id": 0, "password": 0})
    if not user:
        raise APIError("User not found", code="UNAUTHORIZED", status_code=401)
    return user


async def get_admin_session(
    request: Request,
    admin_session: Optional[str] = Cookie(None),
    x_admin_token: Optional[str] = Header(None),
) -> dict:
    token = admin_session or x_admin_token
    if not token:
        raise APIError("Admin session required", code="ADMIN_UNAUTHORIZED",
                       status_code=401)
    db = get_db()
    session = await db.admin_sessions.find_one({"token": token}, {"_id": 0})
    if not session:
        raise APIError("Invalid admin session", code="ADMIN_UNAUTHORIZED",
                       status_code=401)
    expires_at = session.get("expiresAt")
    if isinstance(expires_at, str):
        expires_at = datetime.fromisoformat(expires_at)
    if expires_at and expires_at < datetime.now(timezone.utc):
        await db.admin_sessions.delete_one({"token": token})
        raise APIError("Admin session expired", code="ADMIN_UNAUTHORIZED",
                       status_code=401)
    return session


async def get_optional_user(
    authorization: Optional[str] = Header(None),
) -> Optional[dict]:
    if not authorization:
        return None
    try:
        return await get_current_user(authorization)
    except Exception:
        return None
