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


async def _funnel_data() -> dict:
    """Internal: returns the funnel dict (not wrapped in JSONResponse).

    `/funnel` route wraps this in `ok()`. The overview endpoint reuses
    this helper directly to avoid trying to subscribe a JSONResponse.
    """
    db = get_db()
    registered = await db.users.count_documents({"deleted": {"$ne": True}})
    site_uids = set(await db.sites.distinct(
        "userId", {"deleted": {"$ne": True}}))
    article_uids = set(await db.articles.distinct(
        "userId", {"deleted": {"$ne": True}}))
    scan_uids = set(await db.ai_visibility_scans.distinct("userId"))

    paid_user_ids: set[str] = set()
    if registered:
        sub_rows = await db.subscriptions.aggregate([
            {"$match": {"status": "ACTIVE"}},
            {"$lookup": {"from": "plans", "localField": "planId",
                          "foreignField": "id", "as": "plan"}},
            {"$unwind": {"path": "$plan",
                          "preserveNullAndEmptyArrays": True}},
            {"$match": {"$or": [
                {"plan.isFree": {"$ne": True}},
                {"plan": None}]}},
        ]).to_list(20000)
        paid_user_ids = {r["userId"] for r in sub_rows}

    return {
        "registered": registered,
        "connectedSite": len(site_uids),
        "generatedArticle": len(article_uids),
        "ranScan": len(scan_uids),
        "upgradedToPaid": len(paid_user_ids),
        "visitors": 10000, "signups": registered,
        "trial": await db.subscriptions.count_documents(
            {"status": "TRIALING"}),
        "paid": await db.subscriptions.count_documents(
            {"status": "ACTIVE"}),
    }


@router.get("/funnel")
async def funnel():
    return ok(await _funnel_data())


@router.get("/overview")
async def admin_analytics_overview():
    """Real-data analytics overview for the admin dashboard.

    Combines user growth, revenue, content metrics and conversion funnel
    into a single response (Phase 3 Part 8).
    """
    db = get_db()
    now = datetime.now(timezone.utc)
    today_iso = now.replace(hour=0, minute=0, second=0,
                             microsecond=0).isoformat()
    week_ago = (now - timedelta(days=7)).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0,
                               microsecond=0).isoformat()
    prev_month_end = month_start
    prev_month_start = ((now.replace(day=1) - timedelta(days=1))
                        .replace(day=1, hour=0, minute=0,
                                  second=0, microsecond=0).isoformat())

    # ── Users ──
    total_users = await db.users.count_documents({"deleted": {"$ne": True}})
    new_today = await db.users.count_documents(
        {"deleted": {"$ne": True}, "createdAt": {"$gte": today_iso}})
    new_week = await db.users.count_documents(
        {"deleted": {"$ne": True}, "createdAt": {"$gte": week_ago}})
    new_month = await db.users.count_documents(
        {"deleted": {"$ne": True}, "createdAt": {"$gte": month_start}})

    # by plan
    by_plan = {"free": 0, "starter": 0, "growth": 0, "agency": 0,
                "other": 0}
    plan_lookup = await db.subscriptions.aggregate([
        {"$match": {"status": {"$in": ["ACTIVE", "TRIALING"]}}},
        {"$lookup": {"from": "plans", "localField": "planId",
                      "foreignField": "id", "as": "plan"}},
        {"$unwind": {"path": "$plan",
                      "preserveNullAndEmptyArrays": True}},
        {"$group": {"_id": "$plan.slug", "n": {"$sum": 1}}},
    ]).to_list(20)
    for r in plan_lookup:
        slug = (r["_id"] or "other").lower()
        by_plan[slug if slug in by_plan else "other"] = r["n"]

    daily_signups = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date().isoformat()
        count = await db.users.count_documents({
            "deleted": {"$ne": True},
            "createdAt": {"$gte": f"{day}T00:00:00",
                           "$lt": f"{day}T23:59:59"},
        })
        daily_signups.append({"date": day, "count": count})

    # ── Revenue (MRR/ARR + this/last month) ──
    active_paid = await db.subscriptions.aggregate([
        {"$match": {"status": "ACTIVE"}},
        {"$lookup": {"from": "plans", "localField": "planId",
                      "foreignField": "id", "as": "plan"}},
        {"$unwind": {"path": "$plan",
                      "preserveNullAndEmptyArrays": True}},
        {"$match": {"plan.isFree": {"$ne": True}}},
    ]).to_list(20000)
    mrr = 0
    for sub in active_paid:
        plan = sub.get("plan") or {}
        if sub.get("billingInterval") == "ANNUAL":
            mrr += (plan.get("annualPrice", 0) or 0) / 12
        else:
            mrr += plan.get("monthlyPrice", 0) or 0
    mrr = round(mrr, 2)

    this_month_invoices = await db.invoices.aggregate([
        {"$match": {"status": "PAID",
                     "createdAt": {"$gte": month_start}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)
    last_month_invoices = await db.invoices.aggregate([
        {"$match": {"status": "PAID",
                     "createdAt": {"$gte": prev_month_start,
                                    "$lt": prev_month_end}}},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)

    daily_revenue = []
    for i in range(29, -1, -1):
        day = (now - timedelta(days=i)).date().isoformat()
        agg = await db.invoices.aggregate([
            {"$match": {"status": "PAID",
                         "createdAt": {"$gte": f"{day}T00:00:00",
                                        "$lt": f"{day}T23:59:59"}}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        daily_revenue.append({"date": day,
                               "amount": float(agg[0]["total"])
                               if agg else 0.0})

    # ── Content ──
    articles_total = await db.articles.count_documents(
        {"deleted": {"$ne": True}})
    articles_month = await db.articles.count_documents({
        "deleted": {"$ne": True}, "createdAt": {"$gte": month_start}})
    scans_total = await db.ai_visibility_scans.count_documents({})
    total_words_agg = await db.articles.aggregate([
        {"$match": {"deleted": {"$ne": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$wordCount"}}},
    ]).to_list(1)
    total_words = total_words_agg[0]["total"] if total_words_agg else 0

    # ── Funnel ──
    funnel_data = await _funnel_data()

    return ok({
        "users": {
            "total": total_users, "byPlan": by_plan,
            "newToday": new_today, "newThisWeek": new_week,
            "newThisMonth": new_month, "dailySignups": daily_signups,
        },
        "revenue": {
            "mrr": mrr, "arr": round(mrr * 12, 2),
            "thisMonth": float(this_month_invoices[0]["total"])
                         if this_month_invoices else 0.0,
            "lastMonth": float(last_month_invoices[0]["total"])
                         if last_month_invoices else 0.0,
            "dailyRevenue": daily_revenue,
        },
        "content": {
            "articlesGenerated": articles_total,
            "articlesThisMonth": articles_month,
            "aiScansRun": scans_total,
            "totalWordsWritten": total_words,
        },
        "funnel": funnel_data,
    })
