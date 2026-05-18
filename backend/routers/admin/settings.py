"""Admin: general settings (site name, social links, maintenance, password)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok
from core.security import hash_password, utcnow_iso, verify_password
from routers.admin.auth import _get_admin_credentials

router = APIRouter(prefix="/admin/settings", tags=["admin-settings"],
                   dependencies=[Depends(get_admin_session)])

DEFAULT_SETTINGS = {
    "siteName": "SEO Jalwa", "siteUrl": "https://seojalwa.com",
    "supportEmail": "support@seojalwa.com",
    "contactEmail": "hello@seojalwa.com",
    "twitterUrl": "", "linkedinUrl": "", "instagramUrl": "",
    "maintenanceMode": False, "maintenanceMessage": "",
}


class SettingsBody(BaseModel):
    siteName: Optional[str] = None
    siteUrl: Optional[str] = None
    supportEmail: Optional[str] = None
    contactEmail: Optional[str] = None
    twitterUrl: Optional[str] = None
    linkedinUrl: Optional[str] = None
    instagramUrl: Optional[str] = None
    maintenanceMode: Optional[bool] = None
    maintenanceMessage: Optional[str] = None


class PasswordBody(BaseModel):
    currentPassword: str
    newPassword: str


@router.get("")
async def get_settings():
    doc = await get_db().settings.find_one({"id": "general"}, {"_id": 0})
    if not doc:
        doc = {"id": "general", **DEFAULT_SETTINGS,
               "createdAt": utcnow_iso(), "updatedAt": utcnow_iso()}
        await get_db().settings.insert_one(dict(doc))
        doc.pop("_id", None)
    return ok(doc)


@router.put("")
async def update_settings(body: SettingsBody):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    upd["updatedAt"] = utcnow_iso()
    existing = await get_db().settings.find_one({"id": "general"}, {"_id": 0})
    if existing:
        await get_db().settings.update_one({"id": "general"}, {"$set": upd})
    else:
        await get_db().settings.insert_one(
            {"id": "general", **DEFAULT_SETTINGS, **upd,
             "createdAt": utcnow_iso()})
    return ok({"updated": True})


@router.put("/password")
async def change_password(body: PasswordBody):
    creds = await _get_admin_credentials()
    if not verify_password(body.currentPassword, creds["passwordHash"]):
        raise APIError("Current password incorrect",
                       "INVALID_CREDENTIALS", 401)
    await get_db().admin_credentials.update_one(
        {"id": "admin"},
        {"$set": {"passwordHash": hash_password(body.newPassword),
                  "updatedAt": utcnow_iso()}})
    return ok({"updated": True})
