"""User in-app notifications: list, mark read, unread count."""
from fastapi import APIRouter, Depends

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok, paginate
from core.security import utcnow_iso

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
async def list_notifications(page: int = 1, limit: int = 20,
                             unread_only: bool = False,
                             user=Depends(get_current_user)):
    db = get_db()
    q: dict = {"userId": user["id"]}
    if unread_only:
        q["read"] = False
    total = await db.notifications.count_documents(q)
    rows = await db.notifications.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    # Phase 3 FIX 9 — backfill icon + color on rows persisted before
    # iteration 8 so the frontend always renders a coloured badge.
    from services.notifications import _TYPE_ICON, _TYPE_COLOR
    for r in rows:
        if not r.get("icon"):
            r["icon"] = _TYPE_ICON.get(r.get("type", ""), "bell")
        if not r.get("color"):
            r["color"] = _TYPE_COLOR.get(r.get("type", ""), "gray")
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/unread-count")
async def unread_count(user=Depends(get_current_user)):
    c = await get_db().notifications.count_documents(
        {"userId": user["id"], "read": False})
    return ok({"count": c})


@router.post("/{notif_id}/read")
async def mark_read(notif_id: str, user=Depends(get_current_user)):
    res = await get_db().notifications.update_one(
        {"id": notif_id, "userId": user["id"]},
        {"$set": {"read": True, "readAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Notification not found", "NOT_FOUND", 404)
    return ok({"read": True})


@router.post("/read-all")
async def mark_all_read(user=Depends(get_current_user)):
    res = await get_db().notifications.update_many(
        {"userId": user["id"], "read": False},
        {"$set": {"read": True, "readAt": utcnow_iso()}})
    return ok({"updated": res.modified_count})
