"""Admin: announcements."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, created, ok
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/admin/announcements", tags=["admin-announcements"],
                   dependencies=[Depends(get_admin_session)])


class AnnouncementReq(BaseModel):
    subject: str
    message: str
    # Accept both `targetPlan` (legacy) and `targetAudience` (spec)
    targetPlan: str = "ALL"
    targetAudience: Optional[str] = None
    channel: str = "BOTH"  # IN_APP | EMAIL | BOTH
    channels: Optional[list] = None  # ["EMAIL", "IN_APP"]

    def normalize(self) -> tuple[str, str]:
        plan = (self.targetAudience or self.targetPlan or "ALL").upper()
        if self.channels:
            up = [c.upper() for c in self.channels]
            if "EMAIL" in up and "IN_APP" in up:
                ch = "BOTH"
            elif "EMAIL" in up:
                ch = "EMAIL"
            elif "IN_APP" in up:
                ch = "IN_APP"
            else:
                ch = self.channel.upper()
        else:
            ch = self.channel.upper()
        return plan, ch


async def _audience_user_ids(db, target: str) -> list[str]:
    """Resolve audience target → list of userIds (no PII)."""
    target = target.upper()
    if target == "ALL":
        return await db.users.distinct("id", {"deleted": {"$ne": True}})
    if target == "FREE":
        # Users on a Free plan OR with no active subscription
        free_plan = await db.plans.find_one(
            {"$or": [{"isFree": True}, {"name": "Free"}]},
            {"_id": 0, "id": 1})
        free_uids: set[str] = set()
        if free_plan:
            free_uids.update(await db.subscriptions.distinct(
                "userId", {"planId": free_plan["id"],
                            "status": {"$in": ["ACTIVE", "TRIALING"]}}))
        paid_uids = set(await db.subscriptions.distinct(
            "userId", {"status": {"$in": ["ACTIVE", "TRIALING"]}}))
        all_uids = set(await db.users.distinct(
            "id", {"deleted": {"$ne": True}}))
        no_sub = all_uids - paid_uids
        return list(free_uids | no_sub)
    # Named paid plan
    plan = await db.plans.find_one(
        {"name": target.title()}, {"_id": 0, "id": 1})
    if not plan:
        return []
    return await db.subscriptions.distinct(
        "userId",
        {"planId": plan["id"], "status": {"$in": ["ACTIVE", "TRIALING"]}})


@router.get("/preview-count")
async def preview_count(targetAudience: str = "ALL"):
    """Phase 3 Part 7 — count real recipients before sending."""
    target = targetAudience.upper()
    if target not in {"ALL", "FREE", "STARTER", "GROWTH", "AGENCY"}:
        raise APIError("Invalid target", "INVALID", 400)
    db = get_db()
    user_ids = await _audience_user_ids(db, target)
    return ok({"count": len(user_ids), "targetAudience": target})


@router.post("")
async def send_announcement(body: AnnouncementReq):
    target, channel = body.normalize()
    if target not in {"ALL", "FREE", "STARTER", "GROWTH", "AGENCY"}:
        raise APIError("Invalid target", "INVALID", 400)
    if channel not in {"IN_APP", "EMAIL", "BOTH"}:
        raise APIError("Invalid channel", "INVALID", 400)
    db = get_db()
    user_ids = await _audience_user_ids(db, target)
    recipients = await db.users.find(
        {"id": {"$in": user_ids}, "deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "email": 1}).to_list(20000) if user_ids else []

    channel_norm = channel
    emails_sent = 0
    notifs_created = 0
    if channel_norm in {"EMAIL", "BOTH"}:
        from services import email as _email
        for r in recipients:
            res = await _email.announcement_email(r["email"], body.subject,
                                                  body.message)
            if isinstance(res, dict) and res.get("success"):
                emails_sent += 1

    if channel_norm in {"IN_APP", "BOTH"}:
        from services.notifications import create_notification
        for r in recipients:
            await create_notification(
                r["id"], "ANNOUNCEMENT", body.subject, body.message,
                icon="megaphone", link="/dashboard")
            notifs_created += 1

    doc = {
        "id": str(uuid.uuid4()), "subject": body.subject,
        "message": body.message, "targetPlan": target,
        "targetAudience": target,
        "channel": channel_norm, "channels": [channel_norm]
        if channel_norm != "BOTH" else ["EMAIL", "IN_APP"],
        "sentAt": utcnow_iso(),
        "recipientCount": len(recipients),
        "emailsSent": emails_sent, "notificationsCreated": notifs_created,
        "createdAt": utcnow_iso(),
    }
    await db.announcements.insert_one(dict(doc))
    doc.pop("_id", None)

    from core.audit import log_action
    await log_action(
        "ANNOUNCEMENT_SENT", target_type="announcement",
        target_id=doc["id"],
        metadata={"recipientCount": len(recipients),
                  "targetPlan": target, "channel": channel_norm})

    return created(doc, "Announcement sent")


@router.get("")
async def history():
    rows = await get_db().announcements.find({}, {"_id": 0}).sort(
        "createdAt", -1).to_list(200)
    return ok(rows)
