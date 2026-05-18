"""Admin auth — session-based with hardcoded creds (configurable in settings)."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Cookie, Depends, Request, Response
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.rate_limit import admin_lockout_check
from core.response import APIError, ok
from core.security import create_admin_session_token, utcnow_iso, verify_password, hash_password

router = APIRouter(prefix="/admin/auth", tags=["admin-auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class ChangePasswordReq(BaseModel):
    currentPassword: str
    newPassword: str


async def _get_admin_credentials() -> dict:
    db = get_db()
    rec = await db.admin_credentials.find_one({"id": "admin"}, {"_id": 0})
    if not rec:
        # Seed with env defaults on first read
        rec = {
            "id": "admin",
            "username": os.environ.get("ADMIN_USERNAME", "jalwa"),
            "passwordHash": hash_password(os.environ.get("ADMIN_PASSWORD",
                                                          "jalwaadmin")),
            "createdAt": utcnow_iso(),
        }
        await db.admin_credentials.insert_one(dict(rec))
    return rec


@router.post("/login")
async def admin_login(body: LoginReq, request: Request, response: Response):
    ip = request.client.host if request.client else "anon"
    ok_attempt, retry = admin_lockout_check(ip)
    if not ok_attempt:
        raise APIError(f"Locked out. Retry in {retry}s", "LOCKED_OUT", 429)

    creds = await _get_admin_credentials()
    if body.username != creds["username"] or not verify_password(
            body.password, creds["passwordHash"]):
        raise APIError("Invalid admin credentials",
                       "INVALID_CREDENTIALS", 401)

    token = create_admin_session_token()
    expires = (datetime.now(timezone.utc) + timedelta(hours=2))
    await get_db().admin_sessions.insert_one({
        "id": token, "token": token,
        "ipAddress": ip,
        "expiresAt": expires.isoformat(),
        "createdAt": utcnow_iso(),
    })
    response.set_cookie(
        "admin_session", token,
        httponly=True, samesite="lax",
        max_age=int(timedelta(hours=2).total_seconds()),
    )
    return ok({"token": token, "expiresAt": expires.isoformat()},
              "Admin authenticated")


@router.post("/logout")
async def admin_logout(response: Response,
                       admin_session: str | None = Cookie(None)):
    if admin_session:
        await get_db().admin_sessions.delete_one({"token": admin_session})
    response.delete_cookie("admin_session")
    return ok({"loggedOut": True})


@router.get("/verify")
async def admin_verify(admin=Depends(get_admin_session)):
    return ok({"valid": True, "expiresAt": admin.get("expiresAt")})
