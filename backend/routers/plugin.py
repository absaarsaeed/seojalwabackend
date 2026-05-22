"""WordPress plugin API — called by the plugin, authenticated by X-Jalwa-API-Key header."""
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

from core.database import get_db
from core.response import APIError, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/plugin", tags=["plugin"])

logger = logging.getLogger("jalwa.plugin")


async def _site_from_key(api_key: str | None) -> dict:
    if not api_key:
        logger.warning("plugin auth: missing X-Jalwa-API-Key header")
        raise APIError("Missing API key", "PLUGIN_UNAUTHORIZED", 401)
    suffix = api_key[-4:] if len(api_key) >= 4 else "***"
    site = await get_db().sites.find_one(
        {"apiKey": api_key, "deleted": {"$ne": True}}, {"_id": 0})
    if not site:
        logger.warning("plugin auth: API key not recognised (...%s)", suffix)
        raise APIError("API key not recognised. Re-issue the key from "
                       "your dashboard.", "INVALID_API_KEY", 401)
    logger.info("plugin auth ok: site=%s (key ...%s)",
                site.get("name"), suffix)
    return site


class VerifyReq(BaseModel):
    siteUrl: str | None = None


class ConfirmReq(BaseModel):
    wordpressPostId: str | int
    wordpressUrl: str


class TrackReq(BaseModel):
    pageUrl: str
    event: str = "pageview"


@router.post("/verify")
async def verify(
    request: Request,
    x_jalwa_api_key: str | None = Header(None, alias="X-Jalwa-API-Key"),
):
    # Parse body (best-effort — verify is also callable header-only)
    body: dict = {}
    try:
        body = await request.json() if request.headers.get(
            "content-type", "").startswith("application/json") else {}
    except Exception:
        body = {}

    # Accept api_key from body as a fallback when the header is stripped by
    # an aggressive proxy (some shared hosts strip non-standard headers).
    api_key = x_jalwa_api_key or body.get("api_key") or body.get("apiKey")
    ua = request.headers.get("user-agent", "")
    if api_key:
        logger.info("plugin verify ua=%r keypfx=%s", ua, api_key[:16])
    else:
        logger.warning("plugin verify ua=%r missing key", ua)
        raise APIError("API key required", "MISSING_API_KEY", 400)

    site = await _site_from_key(api_key)

    # Optional siteUrl check (legacy `siteUrl` + new `site_url` both accepted)
    supplied_url = (body.get("siteUrl") or body.get("site_url") or "").strip()
    if supplied_url:
        norm_supplied = supplied_url.rstrip("/").lower().replace(
            "http://", "https://")
        norm_stored = (site.get("url") or "").rstrip("/").lower().replace(
            "http://", "https://")
        if (norm_supplied not in norm_stored
                and norm_stored not in norm_supplied):
            logger.warning(
                "plugin verify: site URL mismatch (supplied=%s stored=%s)",
                norm_supplied, norm_stored)
            raise APIError(
                "API key is registered to a different site. Re-issue the "
                "key from your dashboard for this site.",
                "SITE_URL_MISMATCH", 400)

    wp_version = body.get("wp_version") or body.get("wpVersion") or ""
    php_version = body.get("php_version") or body.get("phpVersion") or ""
    site_name = body.get("site_name") or body.get("siteName") or ""
    plugin_version = body.get("plugin_version") or ""

    updates: dict = {
        "wordpressConnected": True,
        "lastSync": utcnow_iso(),
        "updatedAt": utcnow_iso(),
    }
    if wp_version:
        updates["wordpressVersion"] = wp_version
    if php_version:
        updates["phpVersion"] = php_version
    if supplied_url:
        updates["actualSiteUrl"] = supplied_url
    if plugin_version:
        updates["pluginVersion"] = plugin_version
    if site_name and not site.get("name"):
        updates["name"] = site_name

    # First-connect flag — only fire side effects (notification, email,
    # activity log) the very first time this site successfully connects.
    is_first_connect = not site.get("connectedAt")
    if is_first_connect:
        updates["connectedAt"] = utcnow_iso()

    upd_res = await get_db().sites.update_one(
        {"id": site["id"]}, {"$set": updates})

    logger.info(
        "plugin verify OK site=%s userId=%s wp=%s php=%s "
        "first_connect=%s modified=%s",
        site.get("name"), site.get("userId"),
        wp_version, php_version, is_first_connect,
        upd_res.modified_count)

    # ── First-connect side effects ──────────────────────────────────────
    if is_first_connect:
        # Activity log
        try:
            from services.activity import log_activity
            await log_activity(
                site["userId"], "SITE_CONNECTED",
                metadata={"siteId": site["id"],
                           "siteName": site.get("name") or "",
                           "title": "WordPress connected",
                           "message": (f"{site.get('name')} is now "
                                       "publishing automatically."),
                           "link": "/dashboard/connections",
                           "wpVersion": wp_version,
                           "pluginVersion": plugin_version},
                request=request)
        except Exception as e:
            logger.warning("activity log on first connect failed: %s", e)

        # In-app notification
        try:
            from services.notifications import create_notification
            await create_notification(
                site["userId"], "SITE_CONNECTED",
                "WordPress connected!",
                (f"{site.get('name')} is now connected. Articles will "
                 "publish automatically."),
                icon="check-circle", link="/dashboard/connections")
        except Exception as e:
            logger.warning("notification on first connect failed: %s", e)

        # Email — fire-and-forget so plugin gets a fast 200
        try:
            user_doc = await get_db().users.find_one(
                {"id": site["userId"]},
                {"_id": 0, "email": 1, "fullName": 1})
            if user_doc and user_doc.get("email"):
                from services import email_templates as _et
                from services import email as _email
                rendered = await _et.render_template(
                    "site_connected",
                    {"userName": user_doc.get("fullName") or "there",
                     "siteName": site.get("name") or supplied_url,
                     "siteUrl": site.get("url") or supplied_url,
                     "dashboardUrl": (f"{os.environ.get('FRONTEND_URL', '')}"
                                       "/dashboard/connections")})
                if rendered:
                    import asyncio
                    asyncio.create_task(_email.send_email(
                        user_doc["email"], rendered["subject"],
                        rendered["html"], template="site_connected",
                        user_id=site["userId"]))
        except Exception as e:
            logger.warning("site_connected email failed: %s", e)

    # Kick off auto-analysis if not yet done (best-effort, non-blocking semantics)
    try:
        if not site.get("analyzed"):
            from services.site_analyzer import analyze_and_setup_site
            import asyncio
            asyncio.create_task(analyze_and_setup_site(site["id"]))
    except Exception as e:
        logger.warning("auto-analyzer kickoff failed: %s", e)

    return ok({
        "valid": True,
        "siteName": site["name"],
        "userId": site["userId"],
        "siteId": site["id"],
    })


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
    "plugin_version": "1.0.2",
    "plugin_download_url": "PLACEHOLDER_UPDATE_FROM_ADMIN",
    "plugin_changelog": ("v1.0.2 — Intelligent category selection: each "
                         "published article now lands in the WordPress "
                         "category picked by SEO Jalwa's site analyser, "
                         "with the default category as fallback."),
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
