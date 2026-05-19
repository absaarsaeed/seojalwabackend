"""User profile / settings / account deletion."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import hash_password, utcnow_iso, verify_password
from routers.sites import clean_website_url

router = APIRouter(prefix="/user", tags=["user"])


class ProfileReq(BaseModel):
    fullName: Optional[str] = None
    email: Optional[EmailStr] = None
    websiteUrl: Optional[str] = None


class PasswordReq(BaseModel):
    currentPassword: str
    newPassword: str = Field(min_length=8)


class NotificationsReq(BaseModel):
    emailDigest: Optional[bool] = None
    weeklyScore: Optional[bool] = None
    aiAlerts: Optional[bool] = None
    billingAlerts: Optional[bool] = None


class DeleteReq(BaseModel):
    password: str


@router.put("/profile")
async def update_profile(body: ProfileReq, user=Depends(get_current_user)):
    db = get_db()
    upd: dict = {}

    if body.fullName is not None:
        upd["fullName"] = body.fullName

    if body.email is not None and body.email.lower() != user.get("email"):
        new_email = body.email.lower()
        taken = await db.users.find_one(
            {"email": new_email, "id": {"$ne": user["id"]}})
        if taken:
            raise APIError("Email already in use", "EMAIL_TAKEN", 409)
        upd["email"] = new_email
        # FIX 9: keep emailVerified=True on email change (verification disabled)
        upd["emailVerified"] = True

    old_url = (user.get("websiteUrl") or "").strip()
    if body.websiteUrl is not None:
        new_url = clean_website_url(body.websiteUrl)
        upd["websiteUrl"] = new_url
        if new_url and new_url != old_url:
            # Propagate change to the matching existing Site, if any
            await db.sites.update_one(
                {"userId": user["id"], "url": old_url,
                 "deleted": {"$ne": True}},
                {"$set": {"url": new_url, "updatedAt": utcnow_iso()}})

    if upd:
        upd["updatedAt"] = utcnow_iso()
        await db.users.update_one({"id": user["id"]}, {"$set": upd})

    fresh = await db.users.find_one(
        {"id": user["id"]}, {"_id": 0, "password": 0})
    return ok({"user": fresh, "updated": True}, "Profile updated")


@router.put("/password")
async def update_password(body: PasswordReq, user=Depends(get_current_user)):
    full = await get_db().users.find_one({"id": user["id"]})
    if not full or not verify_password(body.currentPassword,
                                       full.get("password", "")):
        raise APIError("Current password is incorrect",
                       "INVALID_CREDENTIALS", 401)
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"password": hash_password(body.newPassword),
                  "updatedAt": utcnow_iso()}})
    return ok({"updated": True}, "Password updated")


@router.put("/notifications")
async def update_notifications(body: NotificationsReq,
                               user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if upd:
        await get_db().users.update_one(
            {"id": user["id"]}, {"$set": {"notifications": upd,
                                          "updatedAt": utcnow_iso()}})
    return ok({"updated": True})


@router.delete("/account")
async def delete_account(body: DeleteReq, user=Depends(get_current_user)):
    full = await get_db().users.find_one({"id": user["id"]})
    if not full or not verify_password(body.password, full.get("password", "")):
        raise APIError("Password incorrect", "INVALID_CREDENTIALS", 401)
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"deleted": True, "updatedAt": utcnow_iso()}})
    await get_db().subscriptions.update_many(
        {"userId": user["id"], "status": "ACTIVE"},
        {"$set": {"status": "CANCELLED", "cancelAtPeriodEnd": True}})
    return ok({"deleted": True})
