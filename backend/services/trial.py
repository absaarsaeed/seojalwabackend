"""Trial + plan article auto-generation."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from core.database import get_db
from core.security import utcnow_iso
from services.notifications import create_notification

logger = logging.getLogger("jalwa.trial")


async def _read_trial_days() -> int:
    doc = await get_db().settings.find_one({"key": "trial_days"}, {"_id": 0})
    if not doc:
        return 14
    try:
        return int(doc.get("value", 14))
    except Exception:
        return 14


async def _queue(site_id: str, user_id: str, term: str,
                 scheduled_at: datetime) -> str:
    """Insert a SCHEDULED article + queued job; the daily 6 AM cron picks it up."""
    db = get_db()
    article_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    await db.articles.insert_one({
        "id": article_id, "siteId": site_id, "userId": user_id,
        "title": term[:120], "searchTerm": term,
        "status": "SCHEDULED", "scheduledAt": scheduled_at.isoformat(),
        "deleted": False,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    })
    await db.jobs.insert_one({
        "id": job_id, "type": "article-generation",
        "articleId": article_id, "siteId": site_id, "userId": user_id,
        "status": "scheduled", "progress": 0,
        "scheduledAt": scheduled_at.isoformat(),
        "createdAt": utcnow_iso(),
    })
    return article_id


async def setup_trial_articles(user_id: str, site_id: str) -> int:
    """Pre-create `trial_days // 4` (3-7) scheduled articles. Idempotent."""
    db = get_db()
    # Already provisioned?
    existing = await db.articles.count_documents({
        "siteId": site_id, "userId": user_id,
        "source": {"$ne": "USER"}, "deleted": {"$ne": True}})
    if existing >= 3:
        return 0

    trial_days = await _read_trial_days()
    target = max(3, min(7, trial_days // 4))

    terms_cursor = db.search_terms.find(
        {"siteId": site_id, "status": "PENDING"},
        {"_id": 0}).limit(target)
    terms = await terms_cursor.to_list(target)
    while len(terms) < target:
        terms.append({"term": f"Auto topic {len(terms) + 1}"})

    created = 0
    start = datetime.now(timezone.utc)
    for i, t in enumerate(terms[:target]):
        await _queue(site_id, user_id, t["term"],
                      start + timedelta(days=i))
        created += 1

    if created:
        await create_notification(
            user_id, "ARTICLE_PUBLISHED",
            f"{created} trial articles ready",
            (f"We pre-generated {created} articles for your trial. "
             "Review and publish them →"),
            icon="files", link="/dashboard/auto-publish")
        logger.info("trial articles queued: user=%s site=%s count=%d",
                    user_id, site_id, created)
    return created


async def setup_plan_articles(user_id: str, site_id: str,
                              plan_id: str) -> int:
    """First-week batch when a user upgrades to a paid plan."""
    db = get_db()
    plan = await db.plans.find_one({"id": plan_id}, {"_id": 0})
    if not plan:
        return 0
    per_month = int(plan.get("articlesPerMonth", 0) or 0)
    if per_month <= 0:
        return 0
    batch = min(7, per_month)

    terms = await db.search_terms.find(
        {"siteId": site_id, "status": "PENDING"}, {"_id": 0}
    ).limit(batch).to_list(batch)
    while len(terms) < batch:
        terms.append({"term": f"Topic {len(terms) + 1}"})

    start = datetime.now(timezone.utc)
    created = 0
    for i, t in enumerate(terms[:batch]):
        await _queue(site_id, user_id, t["term"], start + timedelta(days=i))
        created += 1

    if created:
        await create_notification(
            user_id, "SUBSCRIPTION_RENEWED",
            f"Welcome to {plan.get('name')}!",
            (f"We pre-generated {created} articles for your first week. "
             "More will roll out automatically."),
            icon="rocket", link="/dashboard/auto-publish")
    return created
