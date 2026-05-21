"""Admin: email logs viewer."""
from typing import Optional

from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok, paginate

router = APIRouter(prefix="/admin/emails", tags=["admin-emails"],
                   dependencies=[Depends(get_admin_session)])


@router.get("")
async def list_emails(page: int = 1, limit: int = 50,
                      status: Optional[str] = None,
                      user_id: Optional[str] = None,
                      template_key: Optional[str] = None):
    db = get_db()
    q: dict = {}
    if status:
        q["status"] = status.upper()
    if user_id:
        q["userId"] = user_id
    if template_key:
        q["templateKey"] = template_key
    total = await db.email_logs.count_documents(q)
    rows = await db.email_logs.find(q, {"_id": 0}).sort(
        "sentAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/{log_id}")
async def get_email(log_id: str):
    doc = await get_db().email_logs.find_one({"id": log_id}, {"_id": 0})
    if not doc:
        raise APIError("Email log not found", "NOT_FOUND", 404)
    return ok(doc)
