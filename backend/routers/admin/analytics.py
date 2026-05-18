"""Admin: analytics (users, revenue, modules, funnel)."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import ok

router = APIRouter(prefix="/admin/analytics", tags=["admin-analytics"],
                   dependencies=[Depends(get_admin_session)])


def _bucket(days_back: int) -> list[str]:
    today = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return [(today - timedelta(days=i)).date().isoformat()
            for i in range(days_back - 1, -1, -1)]


@router.get("/users")
async def users_chart(dateRange: Optional[str] = "30d"):
    days = int(dateRange.rstrip("d")) if dateRange and dateRange.endswith("d") else 30
    buckets = _bucket(days)
    db = get_db()
    rows = await db.users.find(
        {"deleted": {"$ne": True}}, {"_id": 0, "createdAt": 1}).to_list(20000)
    counts = {b: 0 for b in buckets}
    for r in rows:
        d = r.get("createdAt", "")[:10]
        if d in counts:
            counts[d] += 1
    return ok([{"date": d, "count": counts[d]} for d in buckets])


@router.get("/revenue")
async def revenue_chart(dateRange: Optional[str] = "30d"):
    days = int(dateRange.rstrip("d")) if dateRange and dateRange.endswith("d") else 30
    buckets = _bucket(days)
    db = get_db()
    rows = await db.invoices.find(
        {"status": "PAID"},
        {"_id": 0, "createdAt": 1, "amount": 1}).to_list(20000)
    totals = {b: 0.0 for b in buckets}
    for r in rows:
        d = r.get("createdAt", "")[:10]
        if d in totals:
            totals[d] += float(r.get("amount", 0))
    return ok([{"date": d, "revenue": round(totals[d], 2)} for d in buckets])


@router.get("/modules")
async def modules():
    db = get_db()
    return ok({
        "articles": await db.articles.count_documents({}),
        "socialPosts": await db.social_posts.count_documents({}),
        "aiScans": await db.ai_visibility_scans.count_documents({}),
        "generatedContent": await db.generated_content.count_documents({}),
        "brandVoices": await db.brand_voices.count_documents({}),
    })


@router.get("/funnel")
async def funnel():
    db = get_db()
    visitors = 10000  # TODO: hook to real analytics provider
    signups = await db.users.count_documents({"deleted": {"$ne": True}})
    trial = await db.subscriptions.count_documents({"status": "TRIALING"})
    paid = await db.subscriptions.count_documents({"status": "ACTIVE"})
    return ok({"visitors": visitors, "signups": signups,
               "trial": trial, "paid": paid})
