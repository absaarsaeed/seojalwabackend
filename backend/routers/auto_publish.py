"""Auto-publish & publish connection status routes."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/publish", tags=["publish"])


class PublishReq(BaseModel):
    platform: str
    siteId: str


@router.get("/connections/{site_id}")
async def connections(site_id: str, user=Depends(get_current_user)):
    site = await get_db().sites.find_one(
        {"id": site_id, "userId": user["id"]}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    return ok({
        "wordpress": bool(site.get("wordpressConnected")),
        "webflow": bool(site.get("webflowToken")),
        "ghost": bool(site.get("ghostApiKey")),
        "hubspot": bool(site.get("hubspotToken")),
        "wix": bool(site.get("wixApiKey")),
        "notion": bool(site.get("notionToken")),
    })


@router.post("/publish/{article_id}")
async def publish(article_id: str, body: PublishReq,
                  user=Depends(get_current_user)):
    db = get_db()
    article = await db.articles.find_one(
        {"id": article_id, "userId": user["id"]}, {"_id": 0})
    if not article:
        raise APIError("Article not found", "NOT_FOUND", 404)
    site = await db.sites.find_one({"id": body.siteId}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    res = await mocks.publish_to_cms(body.platform.lower(), site, article)
    await db.articles.update_one(
        {"id": article_id},
        {"$set": {"status": "PUBLISHED", "publishedAt": utcnow_iso(),
                  "cmsPostId": res["cmsPostId"], "cmsUrl": res["cmsUrl"]}})
    return ok({"success": True, "url": res["cmsUrl"]})
