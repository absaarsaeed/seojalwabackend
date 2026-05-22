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
    "ANNOUNCEMENT", "SITE_CONNECTED", "SITE_ANALYZED",
    "TRIAL_ARTICLES_READY",
}

# Phase 3 FIX 9 — type → icon + color metadata for the UI badge
_TYPE_ICON = {
    "ARTICLE_PUBLISHED": "file-text",
    "ARTICLE_FAILED": "x-circle",
    "AI_SCAN_COMPLETE": "search",
    "SITE_ANALYZED": "check-circle",
    "TRIAL_ARTICLES_READY": "rocket",
    "SUBSCRIPTION_RENEWED": "credit-card",
    "PAYMENT_FAILED": "alert-circle",
    "NEW_FEATURE_ANNOUNCEMENT": "star",
    "WEEKLY_REPORT_READY": "bar-chart",
    "SITE_CONNECTED": "link",
    "LOW_GROWTH_SCORE": "trending-down",
    "TRIAL_ENDING": "clock",
    "ANNOUNCEMENT": "megaphone",
}
_TYPE_COLOR = {
    "ARTICLE_PUBLISHED": "green",
    "ARTICLE_FAILED": "red",
    "AI_SCAN_COMPLETE": "blue",
    "SITE_ANALYZED": "green",
    "TRIAL_ARTICLES_READY": "purple",
    "SUBSCRIPTION_RENEWED": "green",
    "PAYMENT_FAILED": "red",
    "NEW_FEATURE_ANNOUNCEMENT": "yellow",
    "WEEKLY_REPORT_READY": "blue",
    "SITE_CONNECTED": "green",
    "LOW_GROWTH_SCORE": "orange",
    "TRIAL_ENDING": "orange",
    "ANNOUNCEMENT": "blue",
}


async def create_notification(
    user_id: str,
    type: str,
    title: str,
    message: str = "",
    *,
    icon: str = "",
    color: str = "",
    link: str = "",
) -> str:
    rec_id = str(uuid.uuid4())
    rec = {
        "id": rec_id,
        "userId": user_id,
        "type": type,
        "title": title,
        "message": message,
        "icon": icon or _TYPE_ICON.get(type, "bell"),
        "color": color or _TYPE_COLOR.get(type, "gray"),
        "link": link,
        "read": False,
        "createdAt": utcnow_iso(),
    }
    try:
        await get_db().notifications.insert_one(dict(rec))
    except Exception as e:  # noqa: BLE001
        logger.warning("notification insert failed: %s", e)
    return rec_id
