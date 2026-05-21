"""Daily 9 AM UTC cron — trial-ending + renewal reminders.

Reads `trial_ending_reminder_days` and `renewal_reminder_days` from the
`settings` collection (defaulting to [3, 1] and [7, 3, 1] respectively),
finds subscriptions ending in exactly N days, and queues one notification
+ one email per subscription. Uses a `reminderSent` array on the
subscription doc to avoid duplicates.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from core.database import get_db
from core.security import utcnow_iso
from services import email as email_service
from services.email_templates import render_template
from services.notifications import create_notification

logger = logging.getLogger("jalwa.reminders")


DEFAULT_TRIAL_DAYS = [3, 1]
DEFAULT_RENEWAL_DAYS = [7, 3, 1]


async def _read_days(key: str, default: list[int]) -> list[int]:
    doc = await get_db().settings.find_one({"key": key}, {"_id": 0})
    if not doc:
        return default
    val = doc.get("value")
    if isinstance(val, list) and all(isinstance(x, int) for x in val):
        return val
    return default


async def _users_by_id(user_ids: list[str]) -> dict[str, dict]:
    if not user_ids:
        return {}
    rows = await get_db().users.find(
        {"id": {"$in": user_ids}}, {"_id": 0, "password": 0}).to_list(2000)
    return {r["id"]: r for r in rows}


async def _process(window_days: int, kind: str) -> int:
    """Find subs whose currentPeriodEnd or trialEndsAt is in exactly N days."""
    db = get_db()
    now = datetime.now(timezone.utc)
    lo = (now + timedelta(days=window_days)).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    hi = (now + timedelta(days=window_days + 1)).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    field = "trialEndsAt" if kind == "trial" else "currentPeriodEnd"
    status = "TRIALING" if kind == "trial" else "ACTIVE"
    sent = 0
    cursor = db.subscriptions.find({
        "status": status,
        field: {"$gte": lo, "$lt": hi},
    })
    subs = await cursor.to_list(2000)
    users = await _users_by_id([s["userId"] for s in subs])
    for sub in subs:
        marker = f"{kind}:{window_days}"
        if marker in (sub.get("reminderSent") or []):
            continue
        u = users.get(sub["userId"])
        if not u:
            continue
        plan = await db.plans.find_one(
            {"id": sub.get("planId")}, {"_id": 0}) or {}
        if kind == "trial":
            template_key = "trial_ending"
            vars_ = {"userName": u.get("fullName", "there"),
                     "daysLeft": window_days,
                     "upgradeUrl": "https://seojalwa.com/pricing"}
            notif_title = f"Your trial ends in {window_days} days"
        else:
            template_key = "subscription_expiring"
            vars_ = {"userName": u.get("fullName", "there"),
                     "planName": plan.get("name", "your plan"),
                     "daysLeft": window_days,
                     "renewUrl": "https://seojalwa.com/billing"}
            notif_title = (f"{plan.get('name', 'Your plan')} "
                           f"expires in {window_days} days")

        rendered = await render_template(template_key, vars_)
        if rendered:
            await email_service.send_email(
                u["email"], rendered["subject"], rendered["html"],
                template=template_key, user_id=u["id"])

        await create_notification(
            u["id"], "TRIAL_ENDING" if kind == "trial" else "PAYMENT_FAILED",
            notif_title, "", icon="clock",
            link="/billing" if kind != "trial" else "/pricing")

        await db.subscriptions.update_one(
            {"id": sub["id"]},
            {"$push": {"reminderSent": marker},
             "$set": {"updatedAt": utcnow_iso()}})
        sent += 1
    return sent


async def cron_reminders() -> None:
    """Entry point for APScheduler."""
    trial_days = await _read_days("trial_ending_reminder_days",
                                    DEFAULT_TRIAL_DAYS)
    renewal_days = await _read_days("renewal_reminder_days",
                                     DEFAULT_RENEWAL_DAYS)
    total = 0
    for d in trial_days:
        total += await _process(d, "trial")
    for d in renewal_days:
        total += await _process(d, "renewal")
    logger.info("reminders cron ran — sent=%d trial=%s renewal=%s",
                total, trial_days, renewal_days)
