"""Admin: coupons."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, created, ok
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/admin/coupons", tags=["admin-coupons"],
                   dependencies=[Depends(get_admin_session)])


class CouponReq(BaseModel):
    code: str
    type: str  # PERCENTAGE | FIXED
    value: float
    duration: str = "ONCE"  # ONCE | REPEATING | FOREVER
    maxUses: Optional[int] = None
    expiresAt: Optional[str] = None


class ToggleReq(BaseModel):
    isActive: bool


@router.get("")
async def list_coupons():
    rows = await get_db().coupons.find({}, {"_id": 0}).sort(
        "createdAt", -1).to_list(500)
    return ok(rows)


@router.post("")
async def create_coupon(body: CouponReq):
    if body.type.upper() not in {"PERCENTAGE", "FIXED"}:
        raise APIError("Invalid type", "INVALID", 400)
    doc = {
        "id": str(uuid.uuid4()), "code": body.code.upper(),
        "type": body.type.upper(), "value": body.value,
        "duration": (body.duration or "ONCE").upper(),
        "maxUses": body.maxUses, "usedCount": 0,
        "expiresAt": body.expiresAt, "isActive": True,
        "createdAt": utcnow_iso(),
    }
    await get_db().coupons.insert_one(dict(doc))
    await mocks.lemonsqueezy_create_discount(doc["code"], body.value,
                                              doc["type"])
    doc.pop("_id", None)
    return created(doc)


@router.put("/{coupon_id}")
async def toggle(coupon_id: str, body: ToggleReq):
    res = await get_db().coupons.update_one(
        {"id": coupon_id}, {"$set": {"isActive": body.isActive}})
    if res.matched_count == 0:
        raise APIError("Coupon not found", "NOT_FOUND", 404)
    return ok({"updated": True})


@router.delete("/{coupon_id}")
async def delete_coupon(coupon_id: str):
    res = await get_db().coupons.delete_one({"id": coupon_id})
    if res.deleted_count == 0:
        raise APIError("Coupon not found", "NOT_FOUND", 404)
    return ok({"deleted": True})
