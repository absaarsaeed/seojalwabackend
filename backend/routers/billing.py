"""Billing routes — plans, checkout, subscription, invoices, webhooks."""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/billing", tags=["billing"])


class CheckoutReq(BaseModel):
    planId: str
    interval: str = "monthly"


class CouponReq(BaseModel):
    code: str


@router.get("/plans")
async def billing_plans():
    rows = await get_db().plans.find(
        {"isActive": True}, {"_id": 0}).sort("sortOrder", 1).to_list(50)
    return ok(rows)


@router.post("/checkout")
async def checkout(body: CheckoutReq, user=Depends(get_current_user)):
    plan = await get_db().plans.find_one({"id": body.planId, "isActive": True},
                                         {"_id": 0})
    if not plan:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    res = await mocks.create_checkout(user["id"], body.planId, body.interval)
    return ok(res)


@router.get("/subscription")
async def subscription(user=Depends(get_current_user)):
    sub = await get_db().subscriptions.find_one(
        {"userId": user["id"]}, {"_id": 0},
        sort=[("createdAt", -1)])
    return ok(sub)


@router.post("/cancel")
async def cancel(user=Depends(get_current_user)):
    res = await get_db().subscriptions.update_one(
        {"userId": user["id"], "status": "ACTIVE"},
        {"$set": {"cancelAtPeriodEnd": True, "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("No active subscription", "NOT_FOUND", 404)
    return ok({"cancelAtPeriodEnd": True})


@router.post("/reactivate")
async def reactivate(user=Depends(get_current_user)):
    res = await get_db().subscriptions.update_one(
        {"userId": user["id"], "cancelAtPeriodEnd": True},
        {"$set": {"cancelAtPeriodEnd": False, "status": "ACTIVE",
                  "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("No cancellable subscription", "NOT_FOUND", 404)
    return ok({"reactivated": True})


@router.get("/invoices")
async def invoices(user=Depends(get_current_user)):
    rows = await get_db().invoices.find(
        {"userId": user["id"]}, {"_id": 0}).sort(
        "createdAt", -1).to_list(200)
    return ok(rows)


@router.post("/webhook")
async def webhook(request: Request):
    """Handle LemonSqueezy webhooks.

    TODO: verify X-Signature header HMAC SHA256 against LEMONSQUEEZY_WEBHOOK_SECRET.
    """
    body = await request.json()
    sig = request.headers.get("X-Signature", "")
    raw = await request.body()
    if not mocks.verify_lemonsqueezy_signature(raw, sig):
        raise APIError("Invalid signature", "INVALID_SIGNATURE", 401)

    event = body.get("meta", {}).get("event_name") or body.get("event")
    data = body.get("data", {})
    user_email = data.get("attributes", {}).get("user_email")
    db = get_db()
    user = (await db.users.find_one({"email": user_email}, {"_id": 0})
            if user_email else None)

    if event == "subscription_created" and user:
        plan_id = data.get("attributes", {}).get("variant_id")
        plan = await db.plans.find_one({"lemonSqueezyVariantId": plan_id},
                                       {"_id": 0})
        sub = {
            "id": str(uuid.uuid4()), "userId": user["id"],
            "planId": plan["id"] if plan else None,
            "status": "ACTIVE",
            "lemonSqueezyId": data.get("id"),
            "lemonSqueezySubscriptionId": data.get("id"),
            "currentPeriodStart": utcnow_iso(),
            "currentPeriodEnd": (datetime.now(timezone.utc)
                                 + timedelta(days=30)).isoformat(),
            "cancelAtPeriodEnd": False,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        }
        await db.subscriptions.insert_one(dict(sub))
        await mocks.send_email(user_email, "subscription-started",
                               "You're on a paid plan", "<p>Welcome!</p>")
    elif event in ("subscription_cancelled", "subscription_expired") and user:
        await db.subscriptions.update_many(
            {"userId": user["id"], "status": "ACTIVE"},
            {"$set": {"status": "CANCELLED"
                      if event == "subscription_cancelled" else "EXPIRED",
                      "updatedAt": utcnow_iso()}})
    elif event == "invoice_paid":
        await db.invoices.insert_one({
            "id": str(uuid.uuid4()),
            "userId": user["id"] if user else None,
            "subscriptionId": data.get("attributes", {}).get("subscription_id"),
            "amount": data.get("attributes", {}).get("total_usd", 0),
            "currency": "USD", "status": "PAID",
            "lemonSqueezyInvoiceId": data.get("id"),
            "invoiceUrl": data.get("attributes", {}).get("urls", {}).get("invoice_url"),
            "paidAt": utcnow_iso(), "createdAt": utcnow_iso(),
        })
    elif event == "invoice_payment_failed" and user:
        await mocks.send_email(user_email, "payment-failed",
                               "Payment failed", "<p>Action required.</p>")
    return ok({"received": True})


@router.post("/apply-coupon")
async def apply_coupon(body: CouponReq):
    coupon = await get_db().coupons.find_one(
        {"code": body.code.upper(), "isActive": True}, {"_id": 0})
    if not coupon:
        return ok({"valid": False})
    expires = coupon.get("expiresAt")
    if expires and datetime.fromisoformat(expires) < datetime.now(timezone.utc):
        return ok({"valid": False, "reason": "expired"})
    if coupon.get("maxUses") and coupon.get("usedCount", 0) >= coupon["maxUses"]:
        return ok({"valid": False, "reason": "max_uses"})
    return ok({"valid": True, "discount": coupon["value"],
               "type": coupon["type"]})
