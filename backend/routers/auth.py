"""Auth routes — user registration, login, Google OAuth, refresh, etc."""
import secrets
import time
import urllib.parse
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_current_user
from core.rate_limit import rate_limit
from core.response import APIError, ok, created
from core.security import (
    create_access_token, create_refresh_token, decode_refresh_token,
    hash_password, utcnow_iso, verify_password,
)
import os
from routers.sites import clean_website_url, create_site_from_url
from services import email
from services.config import config_service

router = APIRouter(prefix="/auth", tags=["auth"])

# ------------------------ Google OAuth state cache (CSRF) -----------------
# Maps state token → (expiry_unix_ts, optional return_url). 5-minute TTL.
_GOOGLE_STATE_TTL = 300
_google_states: dict[str, float] = {}


def _store_google_state() -> str:
    state = secrets.token_urlsafe(24)
    _google_states[state] = time.time() + _GOOGLE_STATE_TTL
    # Purge expired entries opportunistically
    now = time.time()
    for k, exp in list(_google_states.items()):
        if exp < now:
            _google_states.pop(k, None)
    return state


def _consume_google_state(state: str) -> bool:
    exp = _google_states.pop(state, None)
    return bool(exp and exp > time.time())


def _google_redirect_uri(request: Request) -> str:
    """Backend callback URL Google will redirect to. Configurable via env."""
    override = os.environ.get("GOOGLE_AUTH_REDIRECT_URI", "").strip()
    if override:
        return override
    # Fall back to the current request's scheme+host
    return f"{request.url.scheme}://{request.url.netloc}/api/auth/google/callback"


class RegisterReq(BaseModel):
    fullName: str
    email: EmailStr
    password: str = Field(min_length=8)
    websiteUrl: Optional[str] = None


class LoginReq(BaseModel):
    email: EmailStr
    password: str


class RefreshReq(BaseModel):
    refreshToken: str


class ForgotReq(BaseModel):
    email: EmailStr


class ResetReq(BaseModel):
    token: str
    newPassword: str = Field(min_length=8)


def _public_user(user: dict) -> dict:
    return {k: v for k, v in user.items() if k not in {"password", "_id"}}


@router.post("/register", dependencies=[Depends(rate_limit("auth_register", 5, 3600))])
async def register(body: RegisterReq):
    db = get_db()
    existing = await db.users.find_one({"email": body.email.lower()})
    if existing:
        raise APIError("Email already registered", "EMAIL_TAKEN", 409)
    user_id = str(uuid.uuid4())
    verify_token = uuid.uuid4().hex
    cleaned_url = clean_website_url(body.websiteUrl) if body.websiteUrl else ""
    doc = {
        "id": user_id,
        "email": body.email.lower(),
        "password": hash_password(body.password),
        "fullName": body.fullName,
        "websiteUrl": cleaned_url,
        "profilePhoto": None, "googleId": None,
        # FIX 9: email verification disabled for now — auto-verified on signup
        "emailVerified": True, "emailVerifyToken": verify_token,
        "resetPasswordToken": None, "resetPasswordExpiry": None,
        "notifications": {"emailDigest": True, "weeklyScore": True,
                          "aiAlerts": True, "billingAlerts": True},
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await db.users.insert_one(dict(doc))

    sites: list[dict] = []
    if cleaned_url:
        try:
            site = await create_site_from_url(user_id, cleaned_url)
            sites.append(site)
        except APIError:
            pass  # Don't block signup if URL fails validation

    await email.welcome_email(
        user_name=body.fullName,
        to=body.email,
        login_url=f"{os.environ.get('FRONTEND_URL', '')}/login",
    )

    return created({
        "user": _public_user(doc),
        "accessToken": create_access_token(user_id),
        "refreshToken": create_refresh_token(user_id),
        "sites": sites,
    }, "Registration successful")


@router.post("/login", dependencies=[Depends(rate_limit("auth_login", 10, 900))])
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


@router.get("/google")
async def google_start(request: Request):
    """Step 1 — generate OAuth state, redirect to Google's consent screen."""
    client_id = await config_service.get_value("google_oauth", "client_id")
    if not client_id:
        raise APIError(
            "Google OAuth is not configured. Ask the admin to add the "
            "Google client_id/secret in the API Keys panel.",
            "GOOGLE_OAUTH_NOT_CONFIGURED", 503)
    state = _store_google_state()
    params = {
        "client_id": client_id,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    }
    url = ("https://accounts.google.com/o/oauth2/v2/auth?"
           + urllib.parse.urlencode(params))
    return RedirectResponse(url, status_code=302)


@router.get("/google/callback")
async def google_callback(request: Request,
                          code: Optional[str] = Query(None),
                          state: Optional[str] = Query(None),
                          error: Optional[str] = Query(None)):
    """Step 2 — exchange code, find/create user, redirect back to frontend."""
    frontend = os.environ.get("FRONTEND_URL", "").rstrip("/")

    def _fail(reason: str):
        target = (f"{frontend}/login?googleError="
                  f"{urllib.parse.quote(reason)}") if frontend else \
            f"/login?googleError={urllib.parse.quote(reason)}"
        return RedirectResponse(target, status_code=302)

    if error:
        return _fail(error)
    if not code or not state or not _consume_google_state(state):
        return _fail("invalid_state_or_code")

    client_id = await config_service.get_value("google_oauth", "client_id")
    client_secret = await config_service.get_value("google_oauth",
                                                   "client_secret")
    if not client_id or not client_secret:
        return _fail("google_oauth_not_configured")

    redirect_uri = _google_redirect_uri(request)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": code, "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                })
            if token_resp.status_code != 200:
                return _fail("token_exchange_failed")
            access_token = token_resp.json().get("access_token", "")
            if not access_token:
                return _fail("no_access_token")

            info_resp = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"})
            if info_resp.status_code != 200:
                return _fail("userinfo_failed")
            info = info_resp.json()
    except Exception:
        return _fail("google_request_failed")

    google_id = info.get("id")
    email_addr = (info.get("email") or "").lower()
    if not google_id or not email_addr:
        return _fail("invalid_google_profile")

    db = get_db()
    user = await db.users.find_one(
        {"$or": [{"googleId": google_id}, {"email": email_addr}],
         "deleted": {"$ne": True}})

    is_new_user = False
    if user:
        # Link Google account if user signed up with email/password originally
        if not user.get("googleId"):
            await db.users.update_one(
                {"id": user["id"]},
                {"$set": {"googleId": google_id,
                          "profilePhoto": info.get("picture")
                          or user.get("profilePhoto"),
                          "emailVerified": True,
                          "updatedAt": utcnow_iso()}})
    else:
        is_new_user = True
        user_id = str(uuid.uuid4())
        user = {
            "id": user_id, "email": email_addr,
            "password": None, "fullName": info.get("name", email_addr),
            "googleId": google_id, "profilePhoto": info.get("picture"),
            "emailVerified": True, "websiteUrl": "",
            "notifications": {"emailDigest": True, "weeklyScore": True,
                              "aiAlerts": True, "billingAlerts": True},
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        }
        await db.users.insert_one(dict(user))

    access_jwt = create_access_token(user["id"])
    refresh_jwt = create_refresh_token(user["id"])

    params = {
        "accessToken": access_jwt,
        "refreshToken": refresh_jwt,
        "isNewUser": "true" if is_new_user else "false",
    }
    target = (f"{frontend}/auth/google/callback?"
              + urllib.parse.urlencode(params)) if frontend else \
        f"/auth/google/callback?{urllib.parse.urlencode(params)}"
    return RedirectResponse(target, status_code=302)


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


_forgot_password_attempts: dict[str, list[float]] = {}


def _forgot_password_rate_limit(email_addr: str) -> None:
    """3 forgot-password requests per email per hour."""
    import time as _time
    now = _time.time()
    bucket = [t for t in _forgot_password_attempts.get(email_addr, [])
              if t > now - 3600]
    if len(bucket) >= 3:
        retry = int(bucket[0] + 3600 - now) + 1
        raise APIError(
            f"Too many password reset requests. Try again in {retry}s.",
            code="RATE_LIMITED", status_code=429)
    bucket.append(now)
    _forgot_password_attempts[email_addr] = bucket


@router.post("/forgot-password")
async def forgot_password(body: ForgotReq):
    _forgot_password_rate_limit(body.email.lower())
    db = get_db()
    user = await db.users.find_one({"email": body.email.lower()})
    if user:
        token = uuid.uuid4().hex
        expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        await db.users.update_one(
            {"id": user["id"]},
            {"$set": {"resetPasswordToken": token,
                      "resetPasswordExpiry": expiry}})
        await email.password_reset(
            user_name=user.get("fullName", "there"),
            to=body.email,
            reset_url=f"{os.environ.get('FRONTEND_URL', '')}/reset-password/{token}",
        )
    # Always return success to avoid email enumeration
    return ok({"sent": True},
              "If this email exists, you will receive a reset link")


@router.post("/reset-password")
async def reset_password(body: ResetReq):
    db = get_db()
    user = await db.users.find_one({"resetPasswordToken": body.token})
    if not user:
        raise APIError("Invalid or expired token", "TOKEN_EXPIRED", 401)
    expiry = user.get("resetPasswordExpiry")
    if expiry and datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise APIError("Token expired", "TOKEN_EXPIRED", 401)
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
    # Enrich subscription with the populated plan object (FIX 3)
    if subscription and subscription.get("planId"):
        plan = await db.plans.find_one(
            {"id": subscription["planId"]}, {"_id": 0})
        if plan:
            subscription["plan"] = plan

    sites = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0}).to_list(100)

    # Auto-migrate: legacy users who signed up with a websiteUrl but have
    # no Site record yet — create one on first /me call after the fix.
    if not sites and (user.get("websiteUrl") or "").strip():
        try:
            site = await create_site_from_url(user["id"], user["websiteUrl"])
            sites = [site]
        except APIError:
            sites = []

    return ok({"user": user, "subscription": subscription, "sites": sites},
              "Current user")
