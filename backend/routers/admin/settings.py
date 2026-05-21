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
    # Plugin distribution settings (stored as separate key/value docs)
    pluginVersion: Optional[str] = None
    pluginDownloadUrl: Optional[str] = None
    pluginChangelog: Optional[str] = None
    # Reminder cron settings — arrays of integer days
    renewalReminderDays: Optional[list[int]] = None
    trialEndingReminderDays: Optional[list[int]] = None
    paymentRetryDays: Optional[list[int]] = None


# Mapping: API field → settings collection key/value record
_PLUGIN_FIELD_MAP = {
    "pluginVersion": "plugin_version",
    "pluginDownloadUrl": "plugin_download_url",
    "pluginChangelog": "plugin_changelog",
    "renewalReminderDays": "renewal_reminder_days",
    "trialEndingReminderDays": "trial_ending_reminder_days",
    "paymentRetryDays": "payment_retry_days",
}


_DEFAULTS_FOR_FIELD = {
    "pluginVersion": "1.0.1",
    "pluginDownloadUrl": "",
    "pluginChangelog": "",
    "renewalReminderDays": [7, 3, 1],
    "trialEndingReminderDays": [3, 1],
    "paymentRetryDays": [1, 3, 7],
}


async def _read_plugin_settings() -> dict:
    db = get_db()
    out: dict = {}
    for api_field, doc_key in _PLUGIN_FIELD_MAP.items():
        doc = await db.settings.find_one({"key": doc_key}, {"_id": 0})
        if doc and doc.get("value") not in (None, ""):
            out[api_field] = doc["value"]
        else:
            out[api_field] = _DEFAULTS_FOR_FIELD.get(api_field, "")
    return out


async def _save_plugin_settings(values: dict) -> None:
    db = get_db()
    for api_field, doc_key in _PLUGIN_FIELD_MAP.items():
        if api_field in values:
            await db.settings.update_one(
                {"key": doc_key},
                {"$set": {"key": doc_key, "value": values[api_field],
                          "updatedAt": utcnow_iso()}},
                upsert=True)


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
    # Merge plugin settings (stored as separate key/value docs)
    doc.update(await _read_plugin_settings())
    return ok(doc)


@router.put("")
async def update_settings(body: SettingsBody):
    all_fields = body.model_dump(exclude_none=True)
    plugin_fields = {k: v for k, v in all_fields.items()
                     if k in _PLUGIN_FIELD_MAP}
    general_fields = {k: v for k, v in all_fields.items()
                      if k not in _PLUGIN_FIELD_MAP}

    if general_fields:
        general_fields["updatedAt"] = utcnow_iso()
        existing = await get_db().settings.find_one(
            {"id": "general"}, {"_id": 0})
        if existing:
            await get_db().settings.update_one(
                {"id": "general"}, {"$set": general_fields})
        else:
            await get_db().settings.insert_one(
                {"id": "general", **DEFAULT_SETTINGS, **general_fields,
                 "createdAt": utcnow_iso()})

    if plugin_fields:
        await _save_plugin_settings(plugin_fields)

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
