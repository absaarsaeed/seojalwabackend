"""Plan-limit enforcement helpers used by generate/scan/post/team routes."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from core.database import get_db
from core.response import APIError


def _month_start_iso() -> str:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).isoformat()


async def _active_subscription(user_id: str) -> Optional[dict]:
    db = get_db()
    return await db.subscriptions.find_one(
        {"userId": user_id, "status": {"$in": ["ACTIVE", "TRIALING"]}},
        {"_id": 0}, sort=[("createdAt", -1)])


async def _plan_for_user(user_id: str) -> tuple[Optional[dict], Optional[dict]]:
    sub = await _active_subscription(user_id)
    if not sub or not sub.get("planId"):
        return sub, None
    plan = await get_db().plans.find_one({"id": sub["planId"]}, {"_id": 0})
    return sub, plan


def _raise_limit(resource: str, used: int, limit: int):
    raise APIError(
        f"{resource.title()} limit reached for the current period",
        code="LIMIT_REACHED", status_code=403,
        meta={"resource": resource, "used": used, "limit": limit,
              "upgrade_url": "/pricing"})


async def check_article_limit(user_id: str) -> dict:
    sub, plan = await _plan_for_user(user_id)
    if not sub:
        raise APIError("No active subscription. Please upgrade.",
                       code="NO_SUBSCRIPTION", status_code=403,
                       meta={"upgrade_url": "/pricing"})
    limit = int((plan or {}).get("articlesPerMonth", 0) or 0)
    if limit <= 0:
        return {"used": 0, "limit": 0, "unlimited": True}
    used = await get_db().articles.count_documents({
        "userId": user_id, "deleted": {"$ne": True},
        "createdAt": {"$gte": _month_start_iso()},
    })
    if used >= limit:
        _raise_limit("articles", used, limit)
    return {"used": used, "limit": limit, "unlimited": False}


async def check_ai_scan_limit(user_id: str) -> dict:
    sub, plan = await _plan_for_user(user_id)
    if not sub:
        raise APIError("No active subscription. Please upgrade.",
                       code="NO_SUBSCRIPTION", status_code=403,
                       meta={"upgrade_url": "/pricing"})
    limit = int((plan or {}).get("aiScansPerMonth", 0) or 0)
    if limit <= 0:
        return {"used": 0, "limit": 0, "unlimited": True}
    used = await get_db().ai_visibility_scans.count_documents({
        "userId": user_id,
        "createdAt": {"$gte": _month_start_iso()},
    })
    if used >= limit:
        _raise_limit("ai_scans", used, limit)
    return {"used": used, "limit": limit, "unlimited": False}


async def check_social_limit(user_id: str) -> dict:
    sub, plan = await _plan_for_user(user_id)
    if not sub:
        raise APIError("No active subscription. Please upgrade.",
                       code="NO_SUBSCRIPTION", status_code=403,
                       meta={"upgrade_url": "/pricing"})
    limit = int((plan or {}).get("socialPostsPerMonth", 0) or 0)
    if limit <= 0:
        return {"used": 0, "limit": 0, "unlimited": True}
    used = await get_db().social_posts.count_documents({
        "userId": user_id,
        "createdAt": {"$gte": _month_start_iso()},
    })
    if used >= limit:
        _raise_limit("social_posts", used, limit)
    return {"used": used, "limit": limit, "unlimited": False}
