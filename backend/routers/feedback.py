"""Public feedback endpoint + admin submissions viewer."""
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_admin_session, get_optional_user
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso
from services import email as email_service

logger = logging.getLogger("jalwa.feedback")


# ─────────────────────────────────────────────────── public endpoint
public_router = APIRouter(tags=["feedback"])


class FeedbackReq(BaseModel):
    message: str = Field(min_length=3)
    rating: Optional[int] = Field(None, ge=1, le=5)
    category: Optional[str] = None
    pageUrl: Optional[str] = None
    email: Optional[EmailStr] = None
    name: Optional[str] = None


@public_router.post("/feedback")
async def submit_feedback(body: FeedbackReq, request: Request,
                          user=Depends(get_optional_user)):
    """Accepts authenticated + anonymous feedback. `email` is optional when
    anonymous — the submission is still recorded; the admin reply flow just
    won't be possible without an address."""
    user_email = ((user or {}).get("email")
                  if user else (body.email or ""))
    doc = {
        "id": str(uuid.uuid4()),
        "type": "FEEDBACK",
        "name": (user or {}).get("fullName") or body.name or "",
        "email": user_email or "",
        "subject": f"Feedback: {body.category or 'general'}",
        "message": body.message,
        "category": body.category or "general",
        "rating": body.rating,
        "pageUrl": body.pageUrl or "",
        "userId": (user or {}).get("id"),
        "status": "NEW",
        "adminNotes": "",
        "ipAddress": request.client.host if request.client else "",
        "createdAt": utcnow_iso(),
    }
    await get_db().submissions.insert_one(dict(doc))
    doc.pop("_id", None)

    try:
        await email_service.send_email(
            "hello@seojalwa.com",
            f"New feedback ({doc['category']})",
            f"<p>From: {doc['name'] or 'Anonymous'} "
            f"&lt;{doc['email'] or 'no email'}&gt;</p>"
            f"<p>Rating: {doc.get('rating','-')}</p>"
            f"<p>Page: {doc['pageUrl']}</p>"
            f"<hr><p>{body.message}</p>",
            template="feedback-notify")
    except Exception:
        pass

    if user:
        try:
            from services.activity import log_activity
            await log_activity(user["id"], "FEEDBACK_SUBMITTED",
                               metadata={"submissionId": doc["id"]},
                               request=request)
        except Exception:
            pass
    return created({"id": doc["id"], "status": "NEW",
                    "message": "Thank you for your feedback!"},
                   "Feedback received")


# ─────────────────────────────────────────────────── admin viewer
admin_router = APIRouter(prefix="/admin/submissions",
                          tags=["admin-submissions"],
                          dependencies=[Depends(get_admin_session)])


class SubmissionUpdate(BaseModel):
    status: Optional[str] = None  # NEW | READ | RESOLVED | REPLIED
    adminNotes: Optional[str] = None


class ReplyReq(BaseModel):
    message: str = Field(min_length=3)


@admin_router.get("")
async def list_submissions(page: int = 1, limit: int = 50,
                           type: Optional[str] = None,
                           status: Optional[str] = None):
    db = get_db()
    q: dict = {}
    if type:
        q["type"] = type.upper()
    if status:
        q["status"] = status.upper()
    total = await db.submissions.count_documents(q)
    rows = await db.submissions.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@admin_router.get("/{sub_id}")
async def get_submission(sub_id: str):
    doc = await get_db().submissions.find_one({"id": sub_id}, {"_id": 0})
    if not doc:
        raise APIError("Submission not found", "NOT_FOUND", 404)
    return ok(doc)


@admin_router.put("/{sub_id}")
async def update_submission(sub_id: str, body: SubmissionUpdate):
    db = get_db()
    upd = body.model_dump(exclude_none=True)
    if not upd:
        raise APIError("Nothing to update", "VALIDATION_ERROR", 422)
    upd["updatedAt"] = utcnow_iso()
    if upd.get("status"):
        upd["status"] = upd["status"].upper()
    res = await db.submissions.update_one({"id": sub_id}, {"$set": upd})
    if res.matched_count == 0:
        raise APIError("Submission not found", "NOT_FOUND", 404)
    return ok({"updated": True})


@admin_router.post("/{sub_id}/reply")
async def reply_to_submission(sub_id: str, body: ReplyReq):
    db = get_db()
    sub = await db.submissions.find_one({"id": sub_id}, {"_id": 0})
    if not sub:
        raise APIError("Submission not found", "NOT_FOUND", 404)
    res = await email_service.send_email(
        sub["email"], f"Re: {sub.get('subject','your message')}",
        f"<p>{body.message}</p>", template="submission-reply")
    await db.submissions.update_one(
        {"id": sub_id},
        {"$set": {"status": "REPLIED", "repliedAt": utcnow_iso(),
                  "updatedAt": utcnow_iso()}})
    return ok({"replied": True, "provider": res.get("provider"),
               "success": res.get("success")})
