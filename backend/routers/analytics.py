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
    """Per-site GSC overview. Always returns valid structure even if
    GSC isn't connected — the frontend uses `gscConnected` to decide
    whether to surface a 'Connect Google Search Console' CTA."""
    db = get_db()
    # All articles for this site (used for both totals and topArticles)
    rows = await db.articles.find(
        {"siteId": siteId, "userId": user["id"],
         "deleted": {"$ne": True}},
        {"_id": 0, "content": 0}).to_list(2000)

    total_clicks = sum(r.get("clicks", 0) or 0 for r in rows)
    total_impr = sum(r.get("impressions", 0) or 0 for r in rows)
    avg_ctr = (total_clicks / total_impr * 100) if total_impr else 0
    pos_vals = [r.get("avgPosition", 0) or 0 for r in rows
                if r.get("avgPosition")]
    avg_pos = (sum(pos_vals) / len(pos_vals)) if pos_vals else 0

    # GSC connection state (encrypted token presence on the user)
    user_doc = await db.users.find_one(
        {"id": user["id"]}, {"_id": 0, "gscAccessToken": 1}) or {}
    gsc_connected = bool(user_doc.get("gscAccessToken"))

    # Trend — compare latest 2 GSC snapshots if we have them
    snaps = await db.gsc_snapshots.find(
        {"siteId": siteId}, {"_id": 0}).sort(
        "syncedAt", -1).limit(2).to_list(2)
    trend = {"impressionsChange": 0.0, "clicksChange": 0.0}
    if len(snaps) >= 2 and snaps[1].get("rowCount"):
        # Crude period-over-period using the snapshot rowCount as a
        # proxy. When real per-period totals are stored, swap in here.
        cur = snaps[0].get("rowCount", 0)
        prev = snaps[1].get("rowCount", 1) or 1
        delta = round(((cur - prev) / prev) * 100, 1)
        trend = {"impressionsChange": delta, "clicksChange": delta}

    top_articles = sorted(
        [r for r in rows if (r.get("clicks") or 0) > 0
         or (r.get("impressions") or 0) > 0],
        key=lambda r: (r.get("clicks") or 0), reverse=True)[:10]
    top_articles = [{
        "id": r["id"], "title": r.get("title", ""),
        "clicks": r.get("clicks", 0),
        "impressions": r.get("impressions", 0),
        "ctr": round((r.get("clicks", 0) / r.get("impressions", 1) * 100)
                     if r.get("impressions") else 0, 2),
        "avgPosition": r.get("avgPosition", 0),
    } for r in top_articles]

    # Top queries from GSC if we've cached them
    top_q_docs = await db.gsc_queries.find(
        {"siteId": siteId}, {"_id": 0}).sort(
        "clicks", -1).limit(10).to_list(10) if "gsc_queries" in (
        await db.list_collection_names()) else []
    top_queries = [{
        "query": q.get("query", ""), "clicks": q.get("clicks", 0),
        "impressions": q.get("impressions", 0),
        "ctr": q.get("ctr", 0), "position": q.get("position", 0),
    } for q in top_q_docs]

    payload = {
        "gscConnected": gsc_connected,
        "metrics": {
            "totalClicks": total_clicks,
            "totalImpressions": total_impr,
            "avgCtr": round(avg_ctr, 2),
            "avgPosition": round(avg_pos, 2),
        },
        "trend": trend,
        "topArticles": top_articles,
        "topQueries": top_queries,
        "dateRange": dateRange,
        # Legacy flat keys (kept for older UI consumers)
        "totalClicks": total_clicks,
        "totalImpressions": total_impr,
        "avgCTR": round(avg_ctr, 2),
        "avgPosition": round(avg_pos, 2),
    }
    if not gsc_connected:
        payload["message"] = ("Connect Google Search Console to see "
                              "real traffic data")
    return ok(payload)


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
