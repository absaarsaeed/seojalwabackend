"""Auth routes — user registration, login, Google OAuth, refresh, etc."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_current_user
from core.rate_limit import rate_limit
from core.response import APIError, ok, created
from core.security import (
    create_access_token, create_refresh_token, decode_refresh_token,
    hash_password, utcnow_iso, verify_password,
)
from services import mocks

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterReq(BaseModel):
    fullName: str
    email: EmailStr
    password: str = Field(min_length=8)
    websiteUrl: Optional[str] = None


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class GoogleReq(BaseModel):
    googleToken: str


class RefreshReq(BaseModel):
    refreshToken: str


class ForgotReq(BaseModel):
    email: EmailStr


class ResetReq(BaseModel):
    token: str
    newPassword: str = Field(min_length=8)


def _public_user(user: dict) -> dict:
    return {k: v for k, v in user.items() if k not in {"password", "_id"}}


@router.post("/register", dependencies=[Depends(rate_limit("auth", 10, 60))])
async def register(body: RegisterReq):
    db = get_db()
    existing = await db.users.find_one({"email": body.email.lower()})
    if existing:
        raise APIError("Email already registered", "EMAIL_TAKEN", 409)
    user_id = str(uuid.uuid4())
    verify_token = uuid.uuid4().hex
    doc = {
        "id": user_id,
        "email": body.email.lower(),
        "password": hash_password(body.password),
        "fullName": body.fullName,
        "profilePhoto": None, "googleId": None,
        "emailVerified": False, "emailVerifyToken": verify_token,
        "resetPasswordToken": None, "resetPasswordExpiry": None,
        "notifications": {"emailDigest": True, "weeklyScore": True,
                          "aiAlerts": True, "billingAlerts": True},
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await db.users.insert_one(dict(doc))

    if body.websiteUrl:
        site_id = str(uuid.uuid4())
        await db.sites.insert_one({
            "id": site_id, "userId": user_id,
            "name": body.websiteUrl, "url": body.websiteUrl,
            "platform": "OTHER", "isActive": True,
            "apiKey": uuid.uuid4().hex, "wordpressConnected": False,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        })

    await mocks.send_email(
        to=body.email, template="welcome",
        subject="Welcome to SEO Jalwa",
        html=f"<p>Welcome {body.fullName}! Verify: /api/auth/verify-email/{verify_token}</p>",
    )

    return created({
        "user": _public_user(doc),
        "accessToken": create_access_token(user_id),
        "refreshToken": create_refresh_token(user_id),
    }, "Registration successful")


@router.post("/login", dependencies=[Depends(rate_limit("auth", 10, 60))])
async def login(body: LoginReq):
    db = get_db()
    user = await db.users.find_one({"email": body.email.lower(),
                                    "deleted": {"$ne": True}})
    if not user or not verify_password(body.password, user["password"]):
        raise APIError("Invalid credentials", "INVALID_CREDENTIALS", 401)
    return ok({
        "user": _public_user(user),
        "accessToken": create_access_token(user["id"]),
        "refreshToken": create_refresh_token(user["id"]),
    }, "Login successful")


@router.post("/google", dependencies=[Depends(rate_limit("auth", 10, 60))])
async def google_login(body: GoogleReq):
    info = await mocks.verify_google_token(body.googleToken)
    if not info:
        raise APIError("Invalid Google token", "INVALID_TOKEN", 401)
    db = get_db()
    user = await db.users.find_one({"email": info["email"].lower()})
    if not user:
        user_id = str(uuid.uuid4())
        user = {
            "id": user_id, "email": info["email"].lower(),
            "password": "", "fullName": info["name"],
            "profilePhoto": info.get("picture"),
            "googleId": info["googleId"], "emailVerified": True,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        }
        await db.users.insert_one(dict(user))
    return ok({
        "user": _public_user(user),
        "accessToken": create_access_token(user["id"]),
        "refreshToken": create_refresh_token(user["id"]),
    }, "Google login successful")


@router.post("/refresh")
async def refresh(body: RefreshReq):
    try:
        payload = decode_refresh_token(body.refreshToken)
    except Exception:
        raise APIError("Invalid refresh token", "INVALID_TOKEN", 401)
    user_id = payload["sub"]
    return ok({"accessToken": create_access_token(user_id)}, "Token refreshed")


@router.post("/logout")
async def logout(user=Depends(get_current_user)):
    # JWT is stateless; in production we'd blacklist refresh JTIs.
    return ok({"loggedOut": True}, "Logged out")


@router.post("/verify-email/{token}")
async def verify_email(token: str):
    db = get_db()
    res = await db.users.update_one(
        {"emailVerifyToken": token},
        {"$set": {"emailVerified": True, "emailVerifyToken": None,
                  "updatedAt": utcnow_iso()}})
    if res.modified_count == 0:
        raise APIError("Invalid or expired token", "INVALID_TOKEN", 400)
    return ok({"verified": True}, "Email verified")


@router.post("/forgot-password",
             dependencies=[Depends(rate_limit("auth", 10, 60))])
async def forgot_password(body: ForgotReq):
    db = get_db()
    user = await db.users.find_one({"email": body.email.lower()})
    if user:
        token = uuid.uuid4().hex
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"resetPasswordToken": token,
                      "resetPasswordExpiry": expiry}})
        await mocks.send_email(
            to=body.email, template="password-reset",
            subject="Reset your password",
            html=f"<p>Reset link: /reset-password?token={token}</p>")
    # Always return success to avoid email enumeration
    return ok({"sent": True}, "If the account exists, an email has been sent")


@router.post("/reset-password")
async def reset_password(body: ResetReq):
    db = get_db()
    user = await db.users.find_one({"resetPasswordToken": body.token})
    if not user:
        raise APIError("Invalid token", "INVALID_TOKEN", 400)
    expiry = user.get("resetPasswordExpiry")
    if expiry and datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise APIError("Token expired", "TOKEN_EXPIRED", 400)
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password": hash_password(body.newPassword),
                  "resetPasswordToken": None,
                  "resetPasswordExpiry": None,
                  "updatedAt": utcnow_iso()}})
    return ok({"reset": True}, "Password reset")


@router.get("/me")
async def me(user=Depends(get_current_user)):
    db = get_db()
    subscription = await db.subscriptions.find_one(
        {"userId": user["id"], "status": {"$in": ["ACTIVE", "TRIALING"]}},
        {"_id": 0})
    sites = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0}).to_list(100)
    return ok({"user": user, "subscription": subscription, "sites": sites},
              "Current user")
