"""In-app notifications writer used by jobs and announcements."""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from core.database import get_db
from core.security import utcnow_iso

logger = logging.getLogger("jalwa.notifications")

VALID_TYPES = {
    "ARTICLE_PUBLISHED", "ARTICLE_FAILED", "AI_SCAN_COMPLETE",
    "LOW_GROWTH_SCORE", "TRIAL_ENDING", "SUBSCRIPTION_RENEWED",
    "PAYMENT_FAILED", "NEW_FEATURE_ANNOUNCEMENT", "WEEKLY_REPORT_READY",
    "ANNOUNCEMENT",
}


async def create_notification(
    user_id: str,
    type: str,
    title: str,
    message: str = "",
    *,
    icon: str = "",
    link: str = "",
) -> str:
    rec_id = str(uuid.uuid4())
    rec = {
        "id": rec_id,
        "userId": user_id,
        "type": type,
        "title": title,
        "message": message,
        "icon": icon,
        "link": link,
        "read": False,
        "createdAt": utcnow_iso(),
    }
    try:
        await get_db().notifications.insert_one(dict(rec))
    except Exception as e:  # noqa: BLE001
        logger.warning("notification insert failed: %s", e)
    return rec_id
