"""Analytics routes — overview, per-article, top search terms / pages."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok, paginate
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/analytics", tags=["analytics"])


class SyncReq(BaseModel):
    siteId: str


class GscConnectReq(BaseModel):
    code: str


@router.get("/overview")
async def overview(siteId: str, dateRange: Optional[str] = "30d",
                   user=Depends(get_current_user)):
    db = get_db()
    rows = await db.articles.find(
        {"siteId": siteId, "userId": user["id"],
         "deleted": {"$ne": True}}, {"_id": 0}).to_list(1000)
    total_clicks = sum(r.get("clicks", 0) for r in rows)
    total_impr = sum(r.get("impressions", 0) for r in rows)
    avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0
    avg_pos = (sum(r.get("avgPosition", 0) or 0 for r in rows)
               / len(rows)) if rows else 0
    return ok({"totalClicks": total_clicks, "totalImpressions": total_impr,
               "avgCTR": round(avg_ctr, 2), "avgPosition": round(avg_pos, 2),
               "dateRange": dateRange})


@router.get("/articles")
async def per_article(siteId: str, page: int = 1, limit: int = 20,
                      dateRange: Optional[str] = "30d",
                      user=Depends(get_current_user)):
    db = get_db()
    q = {"siteId": siteId, "userId": user["id"], "deleted": {"$ne": True}}
    total = await db.articles.count_documents(q)
    rows = await db.articles.find(
        q, {"_id": 0, "content": 0}).sort("clicks", -1).skip(
            (page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/search-terms")
async def top_terms(siteId: str, limit: int = 20,
                    dateRange: Optional[str] = "30d",
                    user=Depends(get_current_user)):
    rows = await get_db().search_terms.find(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}).sort("monthlySearchVolume", -1).limit(limit).to_list(limit)
    return ok(rows)


@router.get("/top-pages")
async def top_pages(siteId: str, limit: int = 20,
                    dateRange: Optional[str] = "30d",
                    user=Depends(get_current_user)):
    rows = await get_db().articles.find(
        {"siteId": siteId, "userId": user["id"],
         "status": "PUBLISHED", "deleted": {"$ne": True}},
        {"_id": 0, "content": 0}).sort("impressions", -1).limit(limit).to_list(limit)
    return ok(rows)


@router.post("/sync")
async def sync(body: SyncReq, user=Depends(get_current_user)):
    site = await get_db().sites.find_one(
        {"id": body.siteId, "userId": user["id"]}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    perf = await mocks.gsc_fetch_performance(site.get("url", ""))
    # Distribute mock impressions/clicks across published articles
    pubs = await get_db().articles.find(
        {"siteId": body.siteId, "status": "PUBLISHED"},
        {"_id": 0, "id": 1}).to_list(1000)
    if pubs:
        for i, a in enumerate(pubs):
            await get_db().articles.update_one({"id": a["id"]}, {"$set": {
                "impressions": int(perf["totalImpressions"] / len(pubs)),
                "clicks": int(perf["totalClicks"] / len(pubs)),
                "ctr": perf["avgCTR"], "avgPosition": perf["avgPosition"],
            }})
    return ok({"synced": True, "lastSync": utcnow_iso(),
               "totalClicks": perf["totalClicks"]})


@router.post("/gsc/connect")
async def gsc_connect(body: GscConnectReq, user=Depends(get_current_user)):
    tokens = await mocks.gsc_exchange_code(body.code)
    from core.encryption import encrypt
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"gscAccessToken": encrypt(tokens["access_token"]),
                  "gscRefreshToken": encrypt(tokens["refresh_token"]),
                  "updatedAt": utcnow_iso()}})
    return ok({"connected": True})
