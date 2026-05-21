"""Analytics routes — overview, per-article, top search terms / pages."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok, paginate
from core.security import utcnow_iso
import os
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
    # Use real GSC sync job (falls back gracefully if no token / not configured)
    from services import jobs as _jobs
    return ok(await _jobs.run_gsc_sync(body.siteId, user["id"]))


@router.post("/gsc/connect")
async def gsc_connect(body: GscConnectReq, user=Depends(get_current_user)):
    """Backward-compat: accept code in POST body (old flow)."""
    from services import gsc as _gsc
    tokens = _gsc.exchange_code(body.code) or {}
    from core.encryption import encrypt
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"gscAccessToken": encrypt(tokens.get("access_token", "")),
                  "gscRefreshToken": encrypt(tokens.get("refresh_token", "")),
                  "gscTokenExpiry": tokens.get("expiry"),
                  "updatedAt": utcnow_iso()}})
    return ok({"connected": bool(tokens.get("access_token"))})


@router.get("/gsc/connect")
async def gsc_authorize(user=Depends(get_current_user)):
    """Step 1 — return the Google authorize URL with state = userId."""
    from services import gsc as _gsc
    state = user["id"]
    url = _gsc.build_authorize_url(state)
    if not url:
        raise APIError("Google OAuth not configured", "GSC_NOT_CONFIGURED",
                       400)
    return ok({"authUrl": url})


@router.get("/gsc/callback")
async def gsc_callback(code: str, state: str):
    """Step 2 — Google redirects here with ?code & ?state=userId."""
    from services import gsc as _gsc
    from core.encryption import encrypt
    tokens = _gsc.exchange_code(code) or {}
    if not tokens.get("access_token"):
        raise APIError("Token exchange failed", "GSC_EXCHANGE_FAILED", 400)
    await get_db().users.update_one(
        {"id": state},
        {"$set": {"gscAccessToken": encrypt(tokens["access_token"]),
                  "gscRefreshToken": encrypt(tokens.get("refresh_token", "")),
                  "gscTokenExpiry": tokens.get("expiry"),
                  "updatedAt": utcnow_iso()}})
    from fastapi.responses import RedirectResponse
    frontend = os.environ.get("FRONTEND_URL", "/")
    return RedirectResponse(url=f"{frontend}/dashboard/analytics?connected=true")
