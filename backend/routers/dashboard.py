"""Unified dashboard overview — metrics + trial banner + recommendations +
recent activity. Single endpoint that powers the user's /dashboard home.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).isoformat()


def _prev_month_window() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    this_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_month.month == 1:
        prev_start = this_month.replace(year=this_month.year - 1, month=12)
    else:
        prev_start = this_month.replace(month=this_month.month - 1)
    return prev_start.isoformat(), this_month.isoformat()


def _trial_info(sub: Optional[dict]) -> Optional[dict]:
    if not sub or sub.get("status") != "TRIALING":
        return None
    ends = sub.get("trialEndsAt") or sub.get("currentPeriodEnd")
    if not ends:
        return None
    try:
        end_dt = datetime.fromisoformat(ends.replace("Z", "+00:00"))
    except Exception:
        return None
    now = datetime.now(timezone.utc)
    days_left = max(0, (end_dt - now).days)
    started = sub.get("currentPeriodStart")
    total = 14
    if started:
        try:
            start_dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
            total = max(1, (end_dt - start_dt).days)
        except Exception:
            pass
    days_used = max(0, total - days_left)
    return {
        "status": "TRIALING",
        "daysLeft": days_left,
        "totalDays": total,
        "daysUsed": days_used,
        "endsAt": ends,
        "urgent": days_left <= 3,
        "expired": days_left <= 0,
    }


def _build_recommendations(site: dict, latest_scan: Optional[dict],
                            growth: Optional[dict],
                            article_count_month: int) -> list[dict]:
    """Heuristic, deterministic suggestions when no AI insights are cached."""
    recs: list[dict] = []

    if not site.get("wordpressConnected"):
        recs.append({
            "id": "connect-wp",
            "icon": "plug",
            "category": "setup",
            "title": "Connect WordPress",
            "description": ("Install the SEO Jalwa plugin to start "
                            "auto-publishing articles to your site."),
            "ctaLabel": "Connect now",
            "ctaUrl": "/dashboard/connections",
        })

    if article_count_month == 0:
        recs.append({
            "id": "first-article",
            "icon": "file-plus",
            "category": "engagement",
            "title": "Generate your first article",
            "description": ("Drop in a keyword and let our AI publish a "
                            "fully-formatted SEO article in minutes."),
            "ctaLabel": "Start writing",
            "ctaUrl": "/dashboard/auto-publish",
        })

    if latest_scan is None:
        recs.append({
            "id": "run-scan",
            "icon": "scan",
            "category": "discovery",
            "title": "Run your first AI Visibility scan",
            "description": ("See whether ChatGPT, Perplexity, Gemini, and "
                            "Claude know about your brand."),
            "ctaLabel": "Run scan",
            "ctaUrl": "/dashboard/ai-visibility",
        })
    elif (latest_scan.get("overallScore") or 0) < 50:
        recs.append({
            "id": "low-visibility",
            "icon": "alert-triangle",
            "category": "alert",
            "title": "Low AI visibility — fix that",
            "description": (f"Your AI visibility score is "
                            f"{latest_scan.get('overallScore', 0)}/100. "
                            "Publish a few branded articles to climb."),
            "ctaLabel": "View report",
            "ctaUrl": "/dashboard/ai-visibility",
        })

    if growth and (growth.get("score") or 0) < 60:
        recs.append({
            "id": "growth-improve",
            "icon": "trending-up",
            "category": "growth",
            "title": "Growth score below 60",
            "description": "Review your settings and brand voice setup.",
            "ctaLabel": "Open settings",
            "ctaUrl": "/dashboard/article-settings",
        })

    return recs[:3]


@router.get("/overview")
async def overview(siteId: Optional[str] = None,
                   user=Depends(get_current_user)):
    db = get_db()

    # Resolve active site
    site_q: dict = {"userId": user["id"], "deleted": {"$ne": True}}
    if siteId:
        site_q["id"] = siteId
    site = await db.sites.find_one(site_q, {"_id": 0},
                                    sort=[("createdAt", 1)])
    if not site:
        raise APIError("No site found", "NO_SITE", 404)

    month_start = _month_start_iso()
    prev_start, this_start = _prev_month_window()

    # Metric: articles this month + delta
    articles_this = await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True},
        "createdAt": {"$gte": month_start}})
    articles_last = await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True},
        "createdAt": {"$gte": prev_start, "$lt": this_start}})
    articles_delta = articles_this - articles_last

    # Metric: scheduled social posts
    social_scheduled = await db.social_posts.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "status": "SCHEDULED"})

    # Metric: latest AI Visibility scan
    latest_scan = await db.ai_visibility_scans.find_one(
        {"siteId": site["id"]}, {"_id": 0}, sort=[("createdAt", -1)])
    visibility_score = (latest_scan or {}).get("overallScore", 0)

    # Metric: total GSC clicks last 30 days (if synced)
    d30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    gsc_total = await db.gsc_daily.aggregate([
        {"$match": {"siteId": site["id"], "date": {"$gte": d30}}},
        {"$group": {"_id": None, "clicks": {"$sum": "$clicks"},
                     "impressions": {"$sum": "$impressions"}}},
    ]).to_list(1)
    total_clicks = (gsc_total[0]["clicks"] if gsc_total else 0)
    total_impressions = (gsc_total[0]["impressions"] if gsc_total else 0)

    # Subscription + trial timeline
    sub = await db.subscriptions.find_one(
        {"userId": user["id"],
         "status": {"$in": ["ACTIVE", "TRIALING"]}}, {"_id": 0},
        sort=[("createdAt", -1)])
    plan = (await db.plans.find_one({"id": sub["planId"]}, {"_id": 0})
            if sub and sub.get("planId") else None)
    if sub and plan:
        sub["plan"] = plan
    trial = _trial_info(sub)

    # Growth score
    growth = await db.growth_scores.find_one(
        {"siteId": site["id"]}, {"_id": 0}, sort=[("createdAt", -1)])

    # Recommendations
    recommendations = _build_recommendations(
        site, latest_scan, growth, articles_this)

    # Recent activity (user_activity_log entries scoped to this user)
    raw_activity = await db.user_activity_log.find(
        {"userId": user["id"]}, {"_id": 0}).sort(
        "createdAt", -1).limit(8).to_list(8)
    recent_activity = [{
        "id": a["id"], "action": a["action"],
        "metadata": a.get("metadata") or {},
        "createdAt": a["createdAt"],
    } for a in raw_activity]

    return ok({
        "site": {
            "id": site["id"], "name": site.get("name"),
            "url": site.get("url"), "platform": site.get("platform"),
            "wordpressConnected": bool(site.get("wordpressConnected")),
            "analyzed": bool(site.get("analyzed")),
        },
        "subscription": sub,
        "trial": trial,
        "metrics": {
            "visibilityScore": visibility_score,
            "articlesThisMonth": articles_this,
            "articlesLastMonth": articles_last,
            "articlesDelta": articles_delta,
            "socialPostsScheduled": social_scheduled,
            "totalClicks": total_clicks,
            "totalImpressions": total_impressions,
            "growthScore": (growth or {}).get("score", 0),
        },
        "recommendations": recommendations,
        "recentActivity": recent_activity,
    })
