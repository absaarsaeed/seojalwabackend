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
@router.get("/version")
async def plugin_version():
    """Public — used by the WordPress plugin's update-check transient."""
    db = get_db()
    rec = await db.settings.find_one({"id": "plugin_version"}, {"_id": 0})
    if not rec:
        rec = {
            "id": "plugin_version",
            "version": "1.0.0",
            "min_wp_version": "5.0",
            "min_php_version": "7.4",
            "changelog": "Initial release",
            "download_url": "https://seojalwa.com/plugin/seojalwa-latest.zip",
            "released_at": "2026-05-19",
        }
        await db.settings.insert_one(dict(rec))
    rec.pop("_id", None)
    return ok({
        "version": rec.get("version", "1.0.0"),
        "min_wp_version": rec.get("min_wp_version", "5.0"),
        "min_php_version": rec.get("min_php_version", "7.4"),
        "changelog": rec.get("changelog", ""),
        "download_url": rec.get("download_url", ""),
        "released_at": rec.get("released_at", ""),
    })
