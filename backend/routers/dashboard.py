"""Unified dashboard overview — metrics + trial banner + recommendations +
recent activity. Single endpoint that powers the user's /dashboard home.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_current_user
from core.response import ok
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
        # Per Master prompt FIX 2 spec
        "isTrialing": True,
        "daysRemaining": days_left,
        "trialEndsAt": ends,
        # Legacy keys retained for any older UI
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
    """Heuristic, deterministic suggestions when no AI insights are cached.

    Each rec exposes both the spec-mandated keys (category, priority, title,
    description, action, link) AND legacy keys (icon, ctaLabel, ctaUrl) so
    older UI keeps working.
    """
    recs: list[dict] = []

    if not site.get("wordpressConnected"):
        recs.append({
            "id": "connect-wp",
            "category": "integration", "priority": "high",
            "icon": "plug",
            "title": "Connect WordPress",
            "description": ("Install the SEO Jalwa plugin to start "
                            "auto-publishing articles to your site."),
            "action": "Connect now", "link": "/dashboard/connections",
            "ctaLabel": "Connect now", "ctaUrl": "/dashboard/connections",
        })

    if article_count_month == 0:
        recs.append({
            "id": "first-article",
            "category": "content", "priority": "high",
            "icon": "file-plus",
            "title": "Generate your first article",
            "description": ("Drop in a keyword and let our AI publish a "
                            "fully-formatted SEO article in minutes."),
            "action": "Start writing", "link": "/dashboard/auto-publish",
            "ctaLabel": "Start writing", "ctaUrl": "/dashboard/auto-publish",
        })

    if latest_scan is None:
        recs.append({
            "id": "run-scan",
            "category": "onboarding", "priority": "high",
            "icon": "scan",
            "title": "Run your first AI Visibility scan",
            "description": ("See whether ChatGPT, Perplexity, Gemini, and "
                            "Claude know about your brand."),
            "action": "Run scan", "link": "/dashboard/ai-visibility",
            "ctaLabel": "Run scan", "ctaUrl": "/dashboard/ai-visibility",
        })
    elif (latest_scan.get("overallScore") or 0) < 50:
        recs.append({
            "id": "low-visibility",
            "category": "optimization", "priority": "medium",
            "icon": "alert-triangle",
            "title": "Low AI visibility — fix that",
            "description": (f"Your AI visibility score is "
                            f"{latest_scan.get('overallScore', 0)}/100. "
                            "Publish a few branded articles to climb."),
            "action": "View report", "link": "/dashboard/ai-visibility",
            "ctaLabel": "View report", "ctaUrl": "/dashboard/ai-visibility",
        })

    if growth and (growth.get("score") or 0) < 60:
        recs.append({
            "id": "growth-improve",
            "category": "optimization", "priority": "medium",
            "icon": "trending-up",
            "title": "Growth score below 60",
            "description": "Review your settings and brand voice setup.",
            "action": "Open settings", "link": "/dashboard/article-settings",
            "ctaLabel": "Open settings",
            "ctaUrl": "/dashboard/article-settings",
        })

    return recs[:3]


@router.get("/overview")
async def overview(siteId: Optional[str] = None,
                   user=Depends(get_current_user)):
    db = get_db()

    # Resolve active site (sort by createdAt asc → first/oldest site)
    site_q: dict = {"userId": user["id"], "deleted": {"$ne": True}}
    if siteId:
        site_q["id"] = siteId
    site = await db.sites.find_one(site_q, {"_id": 0},
                                    sort=[("createdAt", 1)])

    # ── Empty state — user has no site yet (Master prompt FIX 2 spec) ──
    if not site:
        sub = await db.subscriptions.find_one(
            {"userId": user["id"],
             "status": {"$in": ["ACTIVE", "TRIALING"]}}, {"_id": 0},
            sort=[("createdAt", -1)])
        if sub and sub.get("planId"):
            plan = await db.plans.find_one(
                {"id": sub["planId"]}, {"_id": 0})
            if plan:
                sub["plan"] = plan
        return ok({
            "site": None, "subscription": sub,
            "trial": _trial_info(sub),
            "growthScore": {"score": None, "change": 0, "breakdown": {
                "aiVisibility": 0, "seoContent": 0,
                "socialConsistency": 0, "trafficTrend": 0,
            }},
            "metrics": {
                "articlesThisMonth": 0, "articlesPublished": 0,
                "articlesLastMonth": 0, "articlesDelta": 0,
                "socialPostsScheduled": 0,
                "totalClicks": 0, "totalImpressions": 0,
                "avgPosition": 0, "aiVisibilityScore": 0,
                "visibilityScore": 0, "growthScore": 0,
            },
            "recentActivity": [],
            "nextScheduledArticle": None,
            "topPerformingArticle": None,
            "recommendations": [{
                "id": "connect-site",
                "category": "onboarding", "priority": "high",
                "title": "Connect your first site",
                "description": ("Add your website to start generating "
                                "AI-optimised articles."),
                "action": "Connect site", "link": "/dashboard/sites",
                "ctaLabel": "Connect site", "ctaUrl": "/dashboard/sites",
                "icon": "globe",
            }],
            "hasConnectedSite": False,
            "hasGeneratedArticle": False,
            "hasRunScan": False,
            "hasArticleSettings": False,
        })

    month_start = _month_start_iso()
    prev_start, this_start = _prev_month_window()

    # Article counts
    articles_this = await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True},
        "createdAt": {"$gte": month_start}})
    articles_last = await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True},
        "createdAt": {"$gte": prev_start, "$lt": this_start}})
    articles_delta = articles_this - articles_last
    articles_published_total = await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True}, "status": "PUBLISHED"})

    # Scheduled social posts
    social_scheduled = await db.social_posts.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "status": "SCHEDULED"})

    # AI Visibility scan
    latest_scan = await db.ai_visibility_scans.find_one(
        {"siteId": site["id"]}, {"_id": 0}, sort=[("createdAt", -1)])
    visibility_score = (latest_scan or {}).get("overallScore", 0)

    # GSC traffic — sum across articles (kept fresh by daily cron)
    article_traffic = await db.articles.aggregate([
        {"$match": {"siteId": site["id"], "deleted": {"$ne": True}}},
        {"$group": {"_id": None,
                     "clicks": {"$sum": "$clicks"},
                     "impressions": {"$sum": "$impressions"},
                     "avgPos": {"$avg": "$avgPosition"}}},
    ]).to_list(1)
    total_clicks = (article_traffic[0]["clicks"] if article_traffic else 0)
    total_impressions = (article_traffic[0]["impressions"]
                         if article_traffic else 0)
    avg_position = round(article_traffic[0].get("avgPos") or 0, 2) \
        if article_traffic else 0

    # Subscription + trial
    sub = await db.subscriptions.find_one(
        {"userId": user["id"],
         "status": {"$in": ["ACTIVE", "TRIALING"]}}, {"_id": 0},
        sort=[("createdAt", -1)])
    plan = (await db.plans.find_one({"id": sub["planId"]}, {"_id": 0})
            if sub and sub.get("planId") else None)
    if sub and plan:
        sub["plan"] = plan
    trial = _trial_info(sub)

    # Growth score + week-over-week change + breakdown
    growth_history = await db.growth_scores.find(
        {"siteId": site["id"]}, {"_id": 0}).sort(
        "calculatedAt", -1).limit(8).to_list(8)
    growth = growth_history[0] if growth_history else None
    week_ago_iso = (datetime.now(timezone.utc)
                    - timedelta(days=7)).isoformat()
    last_week = next((g for g in growth_history
                      if g.get("calculatedAt", "") < week_ago_iso), None)
    score_change = (((growth or {}).get("score", 0))
                    - ((last_week or {}).get("score", 0))) \
        if (growth and last_week) else 0
    growth_block = {
        "score": (growth or {}).get("score") if growth else None,
        "change": score_change,
        "breakdown": {
            "aiVisibility": (growth or {}).get("aiVisibilityComponent", 0),
            "seoContent": (growth or {}).get("seoContentComponent", 0),
            "socialConsistency": (growth or {}).get(
                "socialConsistencyComponent", 0),
            "trafficTrend": (growth or {}).get("trafficTrendComponent", 0),
        },
    }

    # Next scheduled article
    next_scheduled_doc = await db.articles.find_one({
        "userId": user["id"], "siteId": site["id"],
        "status": "SCHEDULED", "deleted": {"$ne": True},
    }, {"_id": 0, "id": 1, "title": 1, "scheduledAt": 1},
        sort=[("scheduledAt", 1)])
    next_scheduled = ({"id": next_scheduled_doc["id"],
                       "title": next_scheduled_doc.get("title", ""),
                       "scheduledAt": next_scheduled_doc.get("scheduledAt")}
                      if next_scheduled_doc else None)

    # Top performing article (by clicks)
    top_doc = await db.articles.find_one({
        "userId": user["id"], "siteId": site["id"],
        "status": "PUBLISHED", "deleted": {"$ne": True},
        "clicks": {"$gt": 0},
    }, {"_id": 0, "id": 1, "title": 1, "clicks": 1, "impressions": 1},
        sort=[("clicks", -1)])
    top_article = ({"id": top_doc["id"], "title": top_doc.get("title", ""),
                    "clicks": top_doc.get("clicks", 0),
                    "impressions": top_doc.get("impressions", 0)}
                   if top_doc else None)

    # Recommendations
    recommendations = _build_recommendations(
        site, latest_scan, growth, articles_this)

    # Recent activity (last 10)
    raw_activity = await db.user_activity_log.find(
        {"userId": user["id"]}, {"_id": 0}).sort(
        "createdAt", -1).limit(10).to_list(10)
    recent_activity = [{
        "id": a["id"],
        "type": a["action"],
        "action": a["action"],
        "title": (a.get("metadata") or {}).get("title", ""),
        "message": (a.get("metadata") or {}).get("message", ""),
        "metadata": a.get("metadata") or {},
        "timestamp": a["createdAt"],
        "createdAt": a["createdAt"],
        "link": (a.get("metadata") or {}).get("link", ""),
    } for a in raw_activity]

    has_generated = (await db.articles.count_documents({
        "userId": user["id"], "siteId": site["id"],
        "deleted": {"$ne": True}})) > 0
    has_run_scan = latest_scan is not None
    # `hasArticleSettings` is true when a non-default article_settings row
    # exists (analyser writes one, user can also configure manually).
    has_article_settings = (await db.article_settings.count_documents({
        "siteId": site["id"]})) > 0

    return ok({
        "site": {
            "id": site["id"], "name": site.get("name"),
            "url": site.get("url"), "platform": site.get("platform"),
            "wordpressConnected": bool(site.get("wordpressConnected")),
            "analyzed": bool(site.get("analyzed")),
        },
        "subscription": sub,
        "trial": trial,
        "growthScore": growth_block,
        "metrics": {
            "articlesThisMonth": articles_this,
            "articlesLastMonth": articles_last,
            "articlesDelta": articles_delta,
            "articlesPublished": articles_published_total,
            "socialPostsScheduled": social_scheduled,
            "totalClicks": total_clicks,
            "totalImpressions": total_impressions,
            "avgPosition": avg_position,
            "aiVisibilityScore": visibility_score,
            "visibilityScore": visibility_score,  # legacy
            "growthScore": (growth or {}).get("score", 0),  # legacy
        },
        "recommendations": recommendations,
        "recentActivity": recent_activity,
        "nextScheduledArticle": next_scheduled,
        "topPerformingArticle": top_article,
        "hasConnectedSite": True,
        "hasGeneratedArticle": has_generated,
        "hasRunScan": has_run_scan,
        "hasArticleSettings": has_article_settings,
    })
