"""Sites & CMS connections."""
import re
import secrets
import string
import uuid
from typing import Optional
from urllib.parse import urlparse

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

_API_KEY_ALPHABET = string.ascii_letters + string.digits


def generate_site_api_key() -> str:
    """Format: jalwa_live_<32 random alphanumeric chars>."""
    suffix = "".join(secrets.choice(_API_KEY_ALPHABET) for _ in range(32))
    return f"jalwa_live_{suffix}"


def clean_website_url(raw: str) -> str:
    """Ensure https:// prefix and strip trailing slash + whitespace."""
    if not raw:
        return ""
    url = raw.strip()
    # Remove any internal whitespace that might have slipped in from forms
    url = re.sub(r"\s+", "", url)
    if not url:
        return ""
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        url = "https://" + url
    return url.rstrip("/")


def extract_domain(url: str) -> str:
    """Return host without scheme/path/www., e.g. 'maternityfeed.com'."""
    try:
        parsed = urlparse(url if "://" in url else f"https://{url}")
        host = (parsed.netloc or parsed.path or "").lower()
    except Exception:
        host = url.lower()
    host = host.split("/")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or url


async def create_site_from_url(user_id: str, raw_url: str) -> dict:
    """Build & insert a Site record from a raw website URL. Returns the doc."""
    cleaned = clean_website_url(raw_url)
    if not cleaned:
        raise APIError("Invalid website URL", "INVALID_URL", 400)
    name = extract_domain(cleaned) or cleaned
    site = {
        "id": str(uuid.uuid4()), "userId": user_id,
        "name": name, "url": cleaned, "platform": "WORDPRESS",
        "isActive": True, "apiKey": generate_site_api_key(),
        "wordpressConnected": False,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await get_db().sites.insert_one(dict(site))
    site.pop("_id", None)
    return site


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
    if not body.name.strip():
        raise APIError("Site name is required", "VALIDATION_ERROR", 400)
    if not body.url.strip():
        raise APIError("Site URL is required", "VALIDATION_ERROR", 400)
    if body.platform not in PLATFORMS:
        raise APIError("Invalid platform", "INVALID_PLATFORM", 400)
    cleaned_url = clean_website_url(body.url)
    if not cleaned_url:
        raise APIError("Invalid URL format", "INVALID_URL", 400)
    site = {
        "id": str(uuid.uuid4()), "userId": user["id"],
        "name": body.name.strip(), "url": cleaned_url,
        "platform": body.platform,
        "isActive": True, "apiKey": generate_site_api_key(),
        "wordpressConnected": False,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await get_db().sites.insert_one(dict(site))
    site.pop("_id", None)
    try:
        from services.activity import log_activity
        await log_activity(user["id"], "SITE_ADDED",
                            metadata={"siteId": site["id"],
                                       "name": site["name"],
                                       "url": cleaned_url,
                                       "platform": body.platform})
    except Exception:
        pass
    return created(site, "Site created")


@router.post("/migrate-from-profile")
async def migrate_from_profile(user=Depends(get_current_user)):
    """One-shot migration: create a Site from user.websiteUrl if missing."""
    db = get_db()
    existing = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0}).to_list(500)
    if existing:
        return ok({"created": False, "sites": existing,
                   "site": existing[0]},
                  f"{len(existing)} site(s) already exist")
    website_url = (user.get("websiteUrl") or "").strip()
    if not website_url:
        return ok({"created": False, "site": None},
                  "User has no websiteUrl on profile")
    site = await create_site_from_url(user["id"], website_url)
    return ok({"created": True, "site": site, "sites": [site]},
              "Site created from profile websiteUrl")


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

    # Probe the WP plugin status endpoint
    import httpx
    probe_url = f"{site['url'].rstrip('/')}/wp-json/seojalwa/v1/status"
    connected = False
    detail = ""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
            resp = await c.get(probe_url)
            if resp.status_code == 200:
                data = resp.json() if resp.headers.get(
                    "content-type", "").startswith("application/json") else {}
                connected = bool(data.get("connected"))
                detail = data.get("message", "")
    except Exception as e:
        detail = str(e)[:120]

    if connected:
        await db.sites.update_one(
            {"id": site_id},
            {"$set": {"wordpressConnected": True,
                      "lastSync": utcnow_iso(),
                      "updatedAt": utcnow_iso()}})
        # Kick off auto-analysis (idempotent — service checks analyzed flag)
        try:
            from services.site_analyzer import analyze_and_setup_site
            import asyncio
            if not site.get("analyzed"):
                asyncio.create_task(analyze_and_setup_site(site_id))
        except Exception:
            pass
        return ok({"connected": True,
                   "message": "WordPress connected",
                   "lastSync": utcnow_iso()})
    return ok({"connected": False,
               "message": ("Plugin not detected. Make sure plugin is "
                           "installed and API key is entered."),
               "detail": detail})


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
