"""Billing routes — plans, checkout (DUMMY), subscription, invoices, webhooks."""
import logging
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

logger = logging.getLogger("jalwa.billing")


class CheckoutReq(BaseModel):
    planId: str
    billingInterval: str = "MONTHLY"
    couponCode: Optional[str] = None
    # Back-compat with v1: `interval` accepted instead of billingInterval
    interval: Optional[str] = None


class CompleteReq(BaseModel):
    cardNumber: Optional[str] = None
    expiryMonth: Optional[str] = None
    expiryYear: Optional[str] = None
    cvv: Optional[str] = None
    cardName: Optional[str] = None


class CouponReq(BaseModel):
    code: str
    planId: Optional[str] = None
    interval: Optional[str] = None
    billingInterval: Optional[str] = None


def _resolve_price(plan: dict, interval: str) -> int:
    interval_u = (interval or "MONTHLY").upper()
    if interval_u == "ANNUAL":
        return int(plan.get("annualPrice", 0) or 0)
    return int(plan.get("monthlyPrice", 0) or 0)


async def _validate_coupon(code: str, plan: dict,
                            interval: str) -> tuple[dict | None, str | None]:
    db = get_db()
    coupon = await db.coupons.find_one(
        {"code": code.upper(), "isActive": True}, {"_id": 0})
    if not coupon:
        return None, "Coupon not found or inactive"
    expires = coupon.get("expiresAt")
    if expires:
        try:
            exp = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            if exp < datetime.now(timezone.utc):
                return None, "Coupon expired"
        except Exception:
            pass
    if (coupon.get("maxUses") and
            coupon.get("usedCount", 0) >= coupon["maxUses"]):
        return None, "Coupon has reached its usage limit"
    applies = (coupon.get("appliesTo") or "ALL").upper()
    interval_u = (interval or "MONTHLY").upper()
    if applies not in ("ALL", interval_u):
        return None, f"Coupon only valid for {applies.lower()} billing"
    return coupon, None


def _apply_coupon(price: int, coupon: dict) -> tuple[int, int]:
    """Returns (discount_amount, final_price)."""
    ctype = (coupon.get("type") or "").upper()
    val = float(coupon.get("value", 0) or 0)
    if ctype == "PERCENT":
        discount = int(round(price * val / 100))
    elif ctype == "FIXED":
        discount = int(val)
    else:
        discount = 0
    final = max(0, price - discount)
    return discount, final


@router.get("/plans")
async def billing_plans():
    rows = await get_db().plans.find(
        {"isActive": True}, {"_id": 0}).sort(
        [("order", 1), ("sortOrder", 1)]).to_list(50)
    for r in rows:
        if "websiteConnections" not in r and "cmsConnections" in r:
            r["websiteConnections"] = r["cmsConnections"]
        if "cmsConnections" not in r and "websiteConnections" in r:
            r["cmsConnections"] = r["websiteConnections"]
    return ok(rows)


@router.post("/validate-coupon")
async def validate_coupon(body: CouponReq):
    if not body.planId:
        # Generic coupon lookup — used by legacy /apply-coupon UIs
        coupon, err = await _validate_coupon(body.code, {}, "MONTHLY")
        if not coupon:
            return ok({"valid": False, "reason": err})
        return ok({"valid": True, "discount": coupon["value"],
                    "type": coupon["type"], "code": coupon["code"]})
    plan = await get_db().plans.find_one(
        {"id": body.planId, "isActive": True}, {"_id": 0})
    if not plan:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    interval = (body.billingInterval or body.interval or "MONTHLY").upper()
    coupon, err = await _validate_coupon(body.code, plan, interval)
    if not coupon:
        return ok({"valid": False, "reason": err})
    original = _resolve_price(plan, interval)
    discount, final = _apply_coupon(original, coupon)
    return ok({
        "valid": True,
        "code": coupon["code"],
        "discount": {"type": coupon["type"], "value": coupon["value"],
                      "amount": discount},
        "originalPrice": original,
        "finalPrice": final,
    })


@router.post("/checkout")
async def checkout(body: CheckoutReq, user=Depends(get_current_user)):
    plan = await get_db().plans.find_one(
        {"id": body.planId, "isActive": True}, {"_id": 0})
    if not plan:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    interval = (body.billingInterval or body.interval or "MONTHLY").upper()
    if interval not in ("MONTHLY", "ANNUAL"):
        raise APIError("Invalid billingInterval", "INVALID", 400)

    original = _resolve_price(plan, interval)
    discount = 0
    final = original
    coupon_code = None
    if body.couponCode:
        coupon, err = await _validate_coupon(body.couponCode, plan, interval)
        if not coupon:
            raise APIError(err or "Invalid coupon", "INVALID_COUPON", 400)
        discount, final = _apply_coupon(original, coupon)
        coupon_code = coupon["code"]

    session_id = str(uuid.uuid4())
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    doc = {
        "id": session_id, "userId": user["id"],
        "planId": body.planId, "billingInterval": interval,
        "originalPrice": original, "discount": discount,
        "finalPrice": final, "couponCode": coupon_code,
        "status": "PENDING", "expiresAt": expires_at,
        "createdAt": utcnow_iso(),
    }
    await get_db().checkout_sessions.insert_one(dict(doc))
    doc.pop("_id", None)
    return ok({
        "sessionId": session_id, "planName": plan.get("name"),
        "originalPrice": original, "discount": discount,
        "finalPrice": final, "billingInterval": interval,
        "couponCode": coupon_code, "expiresAt": expires_at,
        # Legacy back-compat
        **(await mocks.create_checkout(user["id"], body.planId, interval)),
    })


@router.post("/checkout/{session_id}/complete")
async def complete_checkout(session_id: str, body: CompleteReq,
                             request: Request,
                             user=Depends(get_current_user)):
    """Dummy checkout completion — accepts ANY card details and upgrades.

    NEVER stores or validates real card details. To be replaced when
    LemonSqueezy is wired up.
    """
    db = get_db()
    session = await db.checkout_sessions.find_one(
        {"id": session_id}, {"_id": 0})
    if not session:
        raise APIError("Checkout session not found", "NOT_FOUND", 404)
    if session.get("userId") != user["id"]:
        raise APIError("Checkout session not yours", "FORBIDDEN", 403)
    if session.get("status") != "PENDING":
        raise APIError("Checkout session is no longer pending",
                       "SESSION_CONSUMED", 409)
    try:
        if datetime.fromisoformat(
                session["expiresAt"].replace("Z", "+00:00")) \
                < datetime.now(timezone.utc):
            raise APIError("Checkout session expired", "EXPIRED", 410)
    except APIError:
        raise
    except Exception:
        pass

    plan = await db.plans.find_one({"id": session["planId"]}, {"_id": 0})
    if not plan:
        raise APIError("Plan not found", "NOT_FOUND", 404)

    interval = session.get("billingInterval", "MONTHLY")
    days = 365 if interval == "ANNUAL" else 30
    now_dt = datetime.now(timezone.utc)
    period_end = (now_dt + timedelta(days=days)).isoformat()

    # Upgrade or create the subscription
    existing = await db.subscriptions.find_one(
        {"userId": user["id"]}, {"_id": 0}, sort=[("createdAt", -1)])
    prev_status = (existing or {}).get("status")
    sub_updates = {
        "planId": session["planId"], "status": "ACTIVE",
        "billingInterval": interval,
        "currentPeriodStart": now_dt.isoformat(),
        "currentPeriodEnd": period_end,
        "trialEndsAt": None, "cancelAtPeriodEnd": False,
        "source": "CHECKOUT", "updatedAt": utcnow_iso(),
    }
    if existing:
        await db.subscriptions.update_one(
            {"id": existing["id"]}, {"$set": sub_updates})
        sub_id = existing["id"]
    else:
        sub_id = str(uuid.uuid4())
        await db.subscriptions.insert_one({
            "id": sub_id, "userId": user["id"],
            "createdAt": utcnow_iso(), **sub_updates,
        })

    # Mark coupon used
    if session.get("couponCode"):
        await db.coupons.update_one(
            {"code": session["couponCode"]},
            {"$inc": {"usedCount": 1},
             "$set": {"updatedAt": utcnow_iso()}})

    # Invoice
    invoice_id = str(uuid.uuid4())
    invoice = {
        "id": invoice_id, "userId": user["id"],
        "planId": session["planId"], "subscriptionId": sub_id,
        "amount": session["finalPrice"], "currency": "USD",
        "status": "PAID", "billingInterval": interval,
        "couponCode": session.get("couponCode"),
        "paidAt": utcnow_iso(), "createdAt": utcnow_iso(),
    }
    await db.invoices.insert_one(dict(invoice))

    # Mark session completed
    await db.checkout_sessions.update_one(
        {"id": session_id},
        {"$set": {"status": "COMPLETED",
                  "completedAt": utcnow_iso(),
                  "invoiceId": invoice_id}})

    # Audit + notify
    try:
        from core.audit import log_action
        await log_action(
            "USER_PLAN_CHANGED", target_type="USER", target_id=user["id"],
            ip_address=(request.client.host if request.client else ""),
            changes={"plan": {"from": prev_status,
                               "to": plan.get("name")}},
            metadata={"userEmail": user.get("email"),
                       "invoiceId": invoice_id,
                       "via": "dummy_checkout"})
    except Exception:
        pass
    try:
        from services.notifications import create_notification
        await create_notification(
            user["id"], "SUBSCRIPTION_RENEWED",
            f"Welcome to {plan.get('name')}!",
            (f"Payment of ${session['finalPrice']} successful. "
             f"You're now on the {plan.get('name')} plan."),
            icon="credit-card", link="/dashboard/billing")
    except Exception:
        pass
    try:
        from services import email as _email
        await _email.announcement_email(
            user["email"],
            f"Welcome to the {plan.get('name')} plan!",
            (f"<p>Your payment of ${session['finalPrice']} was successful "
             f"and you're now on the {plan.get('name')} plan.</p>"
             f"<p>Invoice ID: {invoice_id}</p>"))
    except Exception:
        pass

    # Trigger plan-articles batch
    try:
        site = await db.sites.find_one(
            {"userId": user["id"], "deleted": {"$ne": True}},
            {"_id": 0}, sort=[("createdAt", 1)])
        if site:
            from services.trial import setup_plan_articles
            import asyncio as _asyncio
            _asyncio.create_task(setup_plan_articles(
                user["id"], site["id"], session["planId"]))
    except Exception:
        pass

    return ok({
        "message": "Payment successful!",
        "plan": plan.get("name"),
        "invoiceId": invoice_id,
        "subscriptionId": sub_id,
        "amount": session["finalPrice"],
        "billingInterval": interval,
    })


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
    """Legacy alias for /billing/validate-coupon (no planId)."""
    return await validate_coupon(body)
