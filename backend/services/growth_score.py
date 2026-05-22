"""Growth Score calculator — Phase 3 Part 12.

Calculates a 0-100 site growth score from real DB signals:
  * SEO Content (0-25) — articles published × avg seoScore
  * AI Visibility (0-25) — latest scan's overallScore / 4
  * Traffic Trend (0-25) — total clicks (capped) / 10
  * Social Consistency (0-25) — published social posts × 2 (capped)

Designed to be safe to call concurrently — each invocation appends a new
`growth_scores` row tagged with `calculatedAt`. The dashboard reads the
most-recent row and a short trailing history for the trend line.
"""
import logging
import uuid
from datetime import datetime, timezone

from core.database import get_db
from core.security import utcnow_iso

logger = logging.getLogger("jalwa.growth")


async def calculate_growth_score(site_id: str,
                                  user_id: str | None = None) -> int:
    db = get_db()
    site = await db.sites.find_one({"id": site_id}, {"_id": 0})
    if not site:
        return 0
    user_id = user_id or site.get("userId")

    # Component 1 — SEO content
    articles_count = await db.articles.count_documents({
        "siteId": site_id, "status": "PUBLISHED",
        "deleted": {"$ne": True}})
    avg_seo_agg = await db.articles.aggregate([
        {"$match": {"siteId": site_id, "status": "PUBLISHED",
                     "deleted": {"$ne": True}}},
        {"$group": {"_id": None, "avg": {"$avg": "$seoScore"}}},
    ]).to_list(1)
    avg_seo = (avg_seo_agg[0]["avg"] or 0) if avg_seo_agg else 0
    content_component = min(25.0, (articles_count * 2) + (avg_seo / 4))

    # Component 2 — AI visibility
    latest_scan = await db.ai_visibility_scans.find_one(
        {"siteId": site_id}, {"_id": 0},
        sort=[("createdAt", -1)])
    visibility_component = (((latest_scan or {}).get("overallScore", 0) or 0)
                            / 4)
    visibility_component = min(25.0, float(visibility_component))

    # Component 3 — Traffic
    clicks_agg = await db.articles.aggregate([
        {"$match": {"siteId": site_id, "deleted": {"$ne": True}}},
        {"$group": {"_id": None, "total": {"$sum": "$clicks"}}},
    ]).to_list(1)
    total_clicks = (clicks_agg[0]["total"] if clicks_agg else 0) or 0
    traffic_component = min(25.0, total_clicks / 10)

    # Component 4 — Social
    social_count = await db.social_posts.count_documents({
        "siteId": site_id, "status": "PUBLISHED"})
    social_component = min(25.0, social_count * 2)

    total = int(round(
        content_component + visibility_component
        + traffic_component + social_component))

    record = {
        "id": str(uuid.uuid4()),
        "siteId": site_id, "userId": user_id,
        "score": total,
        "seoContentComponent": int(round(content_component)),
        "aiVisibilityComponent": int(round(visibility_component)),
        "trafficTrendComponent": int(round(traffic_component)),
        "socialConsistencyComponent": int(round(social_component)),
        "calculatedAt": utcnow_iso(),
        "createdAt": utcnow_iso(),
    }
    await db.growth_scores.insert_one(dict(record))
    logger.info("growth score site=%s → %s (%s+%s+%s+%s)",
                site_id, total, record["seoContentComponent"],
                record["aiVisibilityComponent"],
                record["trafficTrendComponent"],
                record["socialConsistencyComponent"])
    return total


def schedule_recalc(site_id: str, user_id: str | None = None) -> None:
    """Fire-and-forget background recalculation. Safe to call from any
    sync/async context. Never blocks the caller."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(calculate_growth_score(site_id, user_id))
    except RuntimeError:
        # No running loop — skip silently
        pass
