"""User profile / settings / account deletion."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import hash_password, utcnow_iso, verify_password

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
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()
           if k != "websiteUrl"}
    upd["updatedAt"] = utcnow_iso()
    if upd:
        await get_db().users.update_one(
            {"id": user["id"]}, {"$set": upd})
    return ok({"updated": True}, "Profile updated")


@router.put("/password")
async def update_password(body: PasswordReq, user=Depends(get_current_user)):
    full = await get_db().users.find_one({"id": user["id"]})
    if not full or not verify_password(body.currentPassword,
                                       full.get("password", "")):
        raise APIError("Current password incorrect",
                       "INVALID_CREDENTIALS", 401)
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"password": hash_password(body.newPassword),
                  "updatedAt": utcnow_iso()}})
    return ok({"updated": True})


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
