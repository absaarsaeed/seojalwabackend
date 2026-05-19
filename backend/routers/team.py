"""Team & invitation routes."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, created, ok
from core.security import utcnow_iso
import os
from services import email, mocks

router = APIRouter(prefix="/team", tags=["team"])

ROLES = {"ADMIN", "EDITOR", "VIEWER"}


class InviteReq(BaseModel):
    email: EmailStr
    role: str
    siteIds: list[str] = []
    canAccessBilling: bool = False


class UpdateReq(BaseModel):
    role: Optional[str] = None
    canAccessBilling: Optional[bool] = None
    siteIds: Optional[list[str]] = None


@router.get("")
async def list_team(user=Depends(get_current_user)):
    rows = await get_db().team_members.find(
        {"ownerId": user["id"], "status": {"$ne": "REMOVED"}},
        {"_id": 0}).to_list(200)
    return ok(rows)


@router.post("/invite")
async def invite(body: InviteReq, user=Depends(get_current_user)):
    if body.role.upper() not in ROLES:
        raise APIError("Invalid role", "INVALID_ROLE", 400)
    db = get_db()
    token = uuid.uuid4().hex
    expiry = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    doc = {
        "id": str(uuid.uuid4()), "ownerId": user["id"], "memberId": None,
        "email": body.email.lower(), "role": body.role.upper(),
        "status": "PENDING",
        "canAccessBilling": body.canAccessBilling,
        "inviteToken": token, "inviteExpiry": expiry,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await db.team_members.insert_one(dict(doc))
    for sid in body.siteIds:
        await db.team_site_access.insert_one({
            "id": str(uuid.uuid4()),
            "teamMemberId": doc["id"], "siteId": sid,
        })
    await email.team_invite(
        inviter_name=user.get("fullName", "Someone"),
        workspace_name=user.get("fullName", "their team"),
        to=body.email,
        accept_url=f"{os.environ.get('FRONTEND_URL', '')}/team/accept/{token}",
    )
    doc.pop("_id", None)
    return created(doc, "Invitation sent")


@router.put("/{member_id}")
async def update_member(member_id: str, body: UpdateReq,
                        user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()
           if k != "siteIds"}
    if "role" in upd and upd["role"].upper() not in ROLES:
        raise APIError("Invalid role", "INVALID_ROLE", 400)
    if upd:
        upd["updatedAt"] = utcnow_iso()
        res = await get_db().team_members.update_one(
            {"id": member_id, "ownerId": user["id"]}, {"$set": upd})
        if res.matched_count == 0:
            raise APIError("Member not found", "NOT_FOUND", 404)
    if body.siteIds is not None:
        await get_db().team_site_access.delete_many({"teamMemberId": member_id})
        for sid in body.siteIds:
            await get_db().team_site_access.insert_one({
                "id": str(uuid.uuid4()),
                "teamMemberId": member_id, "siteId": sid,
            })
    return ok({"updated": True})


@router.delete("/{member_id}")
async def remove_member(member_id: str, user=Depends(get_current_user)):
    res = await get_db().team_members.update_one(
        {"id": member_id, "ownerId": user["id"]},
        {"$set": {"status": "REMOVED", "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Member not found", "NOT_FOUND", 404)
    return ok({"removed": True})


@router.get("/accept/{token}")
async def accept(token: str):
    db = get_db()
    member = await db.team_members.find_one(
        {"inviteToken": token, "status": "PENDING"}, {"_id": 0})
    if not member:
        raise APIError("Invalid or used invite", "INVALID_TOKEN", 400)
    expiry = member.get("inviteExpiry")
    if expiry and datetime.fromisoformat(expiry) < datetime.now(timezone.utc):
        raise APIError("Invite expired", "EXPIRED", 400)
    # If user exists, activate; otherwise require signup
    user = await db.users.find_one({"email": member["email"]}, {"_id": 0})
    if not user:
        return ok({"requiresSignup": True, "email": member["email"],
                   "token": token})
    await db.team_members.update_one(
        {"id": member["id"]},
        {"$set": {"memberId": user["id"], "status": "ACTIVE",
                  "inviteToken": None, "updatedAt": utcnow_iso()}})
    return ok({"activated": True})
