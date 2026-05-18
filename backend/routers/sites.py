"""Sites & CMS connections."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.encryption import encrypt
from core.response import APIError, created, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/sites", tags=["sites"])

PLATFORMS = {"WORDPRESS", "SHOPIFY", "WEBFLOW", "GHOST", "WIX", "SQUARESPACE",
             "NEXTJS", "NOTION", "HUBSPOT", "OTHER"}


class SiteCreate(BaseModel):
    name: str
    url: str
    platform: str = "OTHER"


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    platform: Optional[str] = None


class GhostConnect(BaseModel):
    apiKey: str
    siteUrl: str


class OAuthCode(BaseModel):
    code: str


class WixConnect(BaseModel):
    apiKey: str
    siteId: str


@router.get("")
async def list_sites(user=Depends(get_current_user)):
    db = get_db()
    sites = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0}).to_list(500)
    return ok(sites, f"{len(sites)} sites")


@router.post("")
async def create_site(body: SiteCreate, user=Depends(get_current_user)):
    if body.platform not in PLATFORMS:
        raise APIError("Invalid platform", "INVALID_PLATFORM", 400)
    site = {
        "id": str(uuid.uuid4()), "userId": user["id"],
        "name": body.name, "url": body.url, "platform": body.platform,
        "isActive": True, "apiKey": uuid.uuid4().hex,
        "wordpressConnected": False,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await get_db().sites.insert_one(dict(site))
    site.pop("_id", None)
    return created(site, "Site created")


@router.get("/{site_id}")
async def get_site(site_id: str, user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one(
        {"id": site_id, "userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    return ok(site)


@router.put("/{site_id}")
async def update_site(site_id: str, body: SiteUpdate,
                      user=Depends(get_current_user)):
    update = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "platform" in update and update["platform"] not in PLATFORMS:
        raise APIError("Invalid platform", "INVALID_PLATFORM", 400)
    update["updatedAt"] = utcnow_iso()
    db = get_db()
    res = await db.sites.update_one(
        {"id": site_id, "userId": user["id"]}, {"$set": update})
    if res.matched_count == 0:
        raise APIError("Site not found", "NOT_FOUND", 404)
    site = await db.sites.find_one({"id": site_id}, {"_id": 0})
    return ok(site, "Site updated")


@router.delete("/{site_id}")
async def delete_site(site_id: str, user=Depends(get_current_user)):
    res = await get_db().sites.update_one(
        {"id": site_id, "userId": user["id"]},
        {"$set": {"deleted": True, "isActive": False,
                  "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Site not found", "NOT_FOUND", 404)
    return ok({"deleted": True}, "Site deleted")


@router.post("/{site_id}/verify-connection")
async def verify_connection(site_id: str, user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one({"id": site_id, "userId": user["id"]},
                                   {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    return ok({"connected": bool(site.get("wordpressConnected")),
               "lastSync": site.get("lastSync")})


async def _connect_store(site_id: str, user_id: str, fields: dict) -> dict:
    db = get_db()
    res = await db.sites.update_one(
        {"id": site_id, "userId": user_id},
        {"$set": {**fields, "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Site not found", "NOT_FOUND", 404)
    return {"connected": True}


@router.post("/{site_id}/connect/ghost")
async def connect_ghost(site_id: str, body: GhostConnect,
                        user=Depends(get_current_user)):
    return ok(await _connect_store(site_id, user["id"], {
        "ghostApiKey": encrypt(body.apiKey),
        "ghostSiteUrl": body.siteUrl,
    }), "Ghost connected")


@router.post("/{site_id}/connect/webflow")
async def connect_webflow(site_id: str, body: OAuthCode,
                          user=Depends(get_current_user)):
    # TODO: real Webflow OAuth code exchange
    return ok(await _connect_store(site_id, user["id"], {
        "webflowToken": encrypt(f"mock_webflow_{body.code[:10]}"),
    }), "Webflow connected")


@router.post("/{site_id}/connect/hubspot")
async def connect_hubspot(site_id: str, body: OAuthCode,
                          user=Depends(get_current_user)):
    return ok(await _connect_store(site_id, user["id"], {
        "hubspotToken": encrypt(f"mock_hubspot_{body.code[:10]}"),
    }), "HubSpot connected")


@router.post("/{site_id}/connect/wix")
async def connect_wix(site_id: str, body: WixConnect,
                      user=Depends(get_current_user)):
    return ok(await _connect_store(site_id, user["id"], {
        "wixApiKey": encrypt(body.apiKey),
        "wixSiteId": body.siteId,
    }), "Wix connected")


@router.post("/{site_id}/connect/notion")
async def connect_notion(site_id: str, body: OAuthCode,
                         user=Depends(get_current_user)):
    return ok(await _connect_store(site_id, user["id"], {
        "notionToken": encrypt(f"mock_notion_{body.code[:10]}"),
    }), "Notion connected")
