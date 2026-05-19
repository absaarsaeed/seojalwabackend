"""WordPress plugin API — called by the plugin, authenticated by X-Jalwa-API-Key header."""
from datetime import datetime, timezone

from fastapi import APIRouter, Header
from pydantic import BaseModel

from core.database import get_db
from core.response import APIError, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/plugin", tags=["plugin"])


async def _site_from_key(api_key: str | None) -> dict:
    if not api_key:
        raise APIError("Missing API key", "PLUGIN_UNAUTHORIZED", 401)
    site = await get_db().sites.find_one(
        {"apiKey": api_key, "deleted": {"$ne": True}}, {"_id": 0})
    if not site:
        raise APIError("Invalid API key", "PLUGIN_UNAUTHORIZED", 401)
    return site


class ConfirmReq(BaseModel):
    wordpressPostId: str | int
    wordpressUrl: str


class TrackReq(BaseModel):
    pageUrl: str
    event: str = "pageview"


@router.post("/verify")
async def verify(x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key")):
    site = await _site_from_key(x_jalwa_api_key)
    await get_db().sites.update_one(
        {"id": site["id"]},
        {"$set": {"wordpressConnected": True,
                  "lastSync": utcnow_iso(),
                  "updatedAt": utcnow_iso()}})
    return ok({"valid": True, "siteName": site["name"], "userId": site["userId"]})


@router.post("/ping")
async def ping(x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key")):
    site = await _site_from_key(x_jalwa_api_key)
    await get_db().sites.update_one(
        {"id": site["id"]},
        {"$set": {"lastSync": utcnow_iso()}})
    return ok({"pong": True, "lastSync": utcnow_iso()})


@router.get("/articles/pending")
async def pending(x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key")):
    site = await _site_from_key(x_jalwa_api_key)
    now = datetime.now(timezone.utc).isoformat()
    rows = await get_db().articles.find({
        "siteId": site["id"], "status": "SCHEDULED",
        "scheduledAt": {"$lte": now},
    }, {"_id": 0}).to_list(50)
    return ok(rows)


@router.post("/articles/{article_id}/confirm")
async def confirm(article_id: str, body: ConfirmReq,
                  x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key")):
    site = await _site_from_key(x_jalwa_api_key)
    res = await get_db().articles.update_one(
        {"id": article_id, "siteId": site["id"]},
        {"$set": {"status": "PUBLISHED",
                  "publishedAt": utcnow_iso(),
                  "cmsPostId": str(body.wordpressPostId),
                  "cmsUrl": body.wordpressUrl,
                  "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Article not found", "NOT_FOUND", 404)
    return ok({"confirmed": True})


@router.post("/track")
async def track(body: TrackReq,
                x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key")):
    site = await _site_from_key(x_jalwa_api_key)
    await get_db().analytics_events.insert_one({
        "siteId": site["id"], "userId": site["userId"],
        "pageUrl": body.pageUrl, "event": body.event,
        "at": utcnow_iso(),
    })
    return ok({"tracked": True})


# ---------------------------------------------------------------- version
DEFAULT_PLUGIN_SETTINGS = {
    "plugin_version": "1.0.0",
    "plugin_download_url": "PLACEHOLDER_UPDATE_FROM_ADMIN",
    "plugin_changelog": ("Initial release. Automatic daily article "
                         "publishing, SEO optimization, and WordPress "
                         "integration."),
}


async def _plugin_setting(key: str) -> str:
    """Fetch a plugin setting from the `settings` collection (key/value)."""
    doc = await get_db().settings.find_one({"key": key}, {"_id": 0})
    if doc and doc.get("value") not in (None, ""):
        return doc["value"]
    # Lazy-seed default so the admin can edit it later
    default = DEFAULT_PLUGIN_SETTINGS.get(key, "")
    if default:
        await get_db().settings.update_one(
            {"key": key},
            {"$set": {"key": key, "value": default,
                      "updatedAt": utcnow_iso()}},
            upsert=True)
    return default


@router.get("/version")
async def plugin_version():
    """Public — used by the WordPress plugin's update-check transient."""
    version = await _plugin_setting("plugin_version")
    download_url = await _plugin_setting("plugin_download_url")
    changelog = await _plugin_setting("plugin_changelog")
    return ok({
        "version": version or "1.0.0",
        "download_url": download_url if download_url
        and download_url != "PLACEHOLDER_UPDATE_FROM_ADMIN" else "",
        "changelog": changelog or "",
        "min_wp_version": "5.0",
        "min_php_version": "7.4",
        "released_at": "2026-05-19",
    })
