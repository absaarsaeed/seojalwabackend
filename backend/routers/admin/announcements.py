"""Admin: announcements."""
import uuid

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
    targetPlan: str = "ALL"   # ALL | FREE | STARTER | GROWTH | AGENCY
    channel: str = "BOTH"     # IN_APP | EMAIL | BOTH


@router.post("")
async def send_announcement(body: AnnouncementReq):
    if body.targetPlan.upper() not in {"ALL", "FREE", "STARTER", "GROWTH",
                                       "AGENCY"}:
        raise APIError("Invalid target", "INVALID", 400)
    if body.channel.upper() not in {"IN_APP", "EMAIL", "BOTH"}:
        raise APIError("Invalid channel", "INVALID", 400)
    db = get_db()

    # Build recipient list
    target = body.targetPlan.upper()
    recipients: list[dict] = []
    if target == "ALL":
        recipients = await db.users.find(
            {"deleted": {"$ne": True}},
            {"_id": 0, "id": 1, "email": 1}).to_list(20000)
    else:
        plan = await db.plans.find_one({"name": target.title()}, {"_id": 0})
        if plan:
            sub_user_ids = await db.subscriptions.distinct(
                "userId", {"planId": plan["id"], "status": "ACTIVE"})
            recipients = await db.users.find(
                {"id": {"$in": sub_user_ids}, "deleted": {"$ne": True}},
                {"_id": 0, "id": 1, "email": 1}).to_list(20000)
        # FREE = users with no active sub
        if target == "FREE":
            paid = await db.subscriptions.distinct(
                "userId", {"status": "ACTIVE"})
            recipients = await db.users.find(
                {"id": {"$nin": paid}, "deleted": {"$ne": True}},
                {"_id": 0, "id": 1, "email": 1}).to_list(20000)

    channel = body.channel.upper()
    if channel in {"EMAIL", "BOTH"}:
        from services import email as _email
        for r in recipients:
            await _email.announcement_email(r["email"], body.subject,
                                            body.message)
    doc = {
        "id": str(uuid.uuid4()), "subject": body.subject,
        "message": body.message, "targetPlan": target,
        "channel": channel, "sentAt": utcnow_iso(),
        "recipientCount": len(recipients), "createdAt": utcnow_iso(),
    }
    await db.announcements.insert_one(dict(doc))
    doc.pop("_id", None)
    return created(doc, "Announcement sent")


@router.get("")
async def history():
    rows = await get_db().announcements.find({}, {"_id": 0}).sort(
        "createdAt", -1).to_list(200)
    return ok(rows)
