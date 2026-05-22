"""Legal pages — Phase 3 Part 6.

Three static legal pages (privacy, terms, cookies) stored in the
`legal_pages` collection and editable from the admin panel.
"""
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok
from core.security import utcnow_iso

public_router = APIRouter(prefix="/legal", tags=["legal"])
admin_router = APIRouter(prefix="/admin/legal", tags=["admin-legal"],
                          dependencies=[Depends(get_admin_session)])

ALLOWED_KEYS = {"privacy-policy", "terms-of-service", "cookie-policy"}

DEFAULT_PAGES = [
    {"key": "privacy-policy", "title": "Privacy Policy",
     "content": ("<h1>Privacy Policy</h1>"
                  "<p><em>Last updated:</em> {today}</p>"
                  "<p>Add your privacy policy here. The admin panel lets "
                  "you replace this placeholder with the real content "
                  "approved by your legal team.</p>")},
    {"key": "terms-of-service", "title": "Terms of Service",
     "content": ("<h1>Terms of Service</h1>"
                  "<p><em>Last updated:</em> {today}</p>"
                  "<p>Add your terms of service here.</p>")},
    {"key": "cookie-policy", "title": "Cookie Policy",
     "content": ("<h1>Cookie Policy</h1>"
                  "<p><em>Last updated:</em> {today}</p>"
                  "<p>Add your cookie policy here.</p>")},
]


async def seed_legal_pages():
    db = get_db()
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    for page in DEFAULT_PAGES:
        existing = await db.legal_pages.find_one(
            {"key": page["key"]}, {"_id": 0})
        if not existing:
            doc = dict(page)
            doc["content"] = doc["content"].format(today=today)
            doc["lastUpdatedAt"] = utcnow_iso()
            doc["createdAt"] = utcnow_iso()
            await db.legal_pages.insert_one(doc)


class LegalPageReq(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None


@public_router.get("/{key}")
async def get_legal(key: str):
    if key not in ALLOWED_KEYS:
        raise APIError("Page not found", "NOT_FOUND", 404)
    page = await get_db().legal_pages.find_one({"key": key}, {"_id": 0})
    if not page:
        raise APIError("Page not yet seeded", "NOT_FOUND", 404)
    page["slug"] = page.get("key")
    return ok(page)


@admin_router.get("")
async def list_legal():
    rows = await get_db().legal_pages.find({}, {"_id": 0}).to_list(20)
    # Return both `key` and `slug` so frontend can use either name
    for r in rows:
        r["slug"] = r.get("key")
    return ok(rows)


@admin_router.get("/{key}")
async def get_legal_admin(key: str):
    if key not in ALLOWED_KEYS:
        raise APIError("Invalid page key", "INVALID_KEY", 400)
    page = await get_db().legal_pages.find_one({"key": key}, {"_id": 0})
    if not page:
        raise APIError("Page not yet seeded", "NOT_FOUND", 404)
    page["slug"] = page.get("key")
    return ok(page)


@admin_router.put("/{key}")
async def update_legal(key: str, body: LegalPageReq):
    if key not in ALLOWED_KEYS:
        raise APIError("Invalid page key", "INVALID_KEY", 400)
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    upd["lastUpdatedAt"] = utcnow_iso()
    upd["updatedAt"] = utcnow_iso()
    res = await get_db().legal_pages.update_one(
        {"key": key}, {"$set": upd}, upsert=True)
    if not res.acknowledged:
        raise APIError("Update failed", "UPDATE_FAILED", 500)
    page = await get_db().legal_pages.find_one({"key": key}, {"_id": 0})
    page["slug"] = page.get("key")

    # Audit log (Phase 3 FIX 1)
    try:
        from core.audit import log_action
        await log_action(
            "LEGAL_PAGE_UPDATED", target_type="LEGAL_PAGE",
            target_id=key,
            changes={"slug": key, "action": "content_updated",
                      "title": upd.get("title")})
    except Exception:
        pass
    return ok({"updated": True, "page": page})
