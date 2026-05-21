"""User activity log writer + reader.

Every notable user-facing action calls `log_activity(user_id, action, ...)`
which inserts into the `user_activity_log` collection. Used by both the
admin dashboard (per-user timeline) and the user's own activity feed.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import Request

from core.database import get_db
from core.security import utcnow_iso

logger = logging.getLogger("jalwa.activity")

# Single source of truth — kept short on purpose; expand as needed.
ACTIONS = {
    "USER_REGISTERED", "USER_LOGGED_IN", "USER_LOGGED_OUT",
    "USER_PASSWORD_CHANGED", "USER_PROFILE_UPDATED",
    "SITE_ADDED", "SITE_CONNECTED", "SITE_DELETED",
    "ARTICLE_GENERATED", "ARTICLE_PUBLISHED", "ARTICLE_FAILED",
    "AI_SCAN_RUN", "BRAND_VOICE_TRAINED",
    "SEARCH_TERMS_ADDED", "SETTINGS_UPDATED",
    "SUBSCRIPTION_UPGRADED", "SUBSCRIPTION_DOWNGRADED",
    "SUBSCRIPTION_CANCELLED", "FEEDBACK_SUBMITTED",
}


async def log_activity(
    user_id: str,
    action: str,
    *,
    metadata: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> str:
    rec_id = str(uuid.uuid4())
    ip = ""
    ua = ""
    if request is not None:
        ip = (request.client.host if request.client else "") or ""
        ua = request.headers.get("user-agent", "")[:200]
    rec = {
        "id": rec_id,
        "userId": user_id,
        "action": action,
        "metadata": metadata or {},
        "ipAddress": ip,
        "userAgent": ua,
        "createdAt": utcnow_iso(),
    }
    try:
        await get_db().user_activity_log.insert_one(dict(rec))
    except Exception as e:  # noqa: BLE001
        logger.warning("activity log insert failed (%s): %s", action, e)
    return rec_id


async def list_activity(
    user_id: str,
    *,
    page: int = 1,
    limit: int = 50,
    action: Optional[str] = None,
) -> tuple[list[dict], int]:
    db = get_db()
    q: dict = {"userId": user_id}
    if action:
        q["action"] = action
    total = await db.user_activity_log.count_documents(q)
    rows = await db.user_activity_log.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return rows, total
