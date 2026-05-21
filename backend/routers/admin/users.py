"""Admin dashboard + users management."""
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from core.audit import log_action
from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok, paginate
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/admin", tags=["admin"])


class PlanChangeReq(BaseModel):
    planId: str


class StatusReq(BaseModel):
    status: str  # active | suspended


class TrialReq(BaseModel):
    days: int


class NoteReq(BaseModel):
    note: str


class SubscriptionUpdateReq(BaseModel):
    planId: Optional[str] = None
    status: Optional[str] = None
    billingInterval: Optional[str] = None
    trialDays: Optional[int] = None
    adminNote: Optional[str] = None


# ============================ DASHBOARD ===================================

@router.get("/dashboard/stats", dependencies=[Depends(get_admin_session)])
async def stats():
    db = get_db()
    now = datetime.now(timezone.utc)
    today_iso = now.replace(hour=0, minute=0, second=0).isoformat()
    week_iso = (now - timedelta(days=7)).isoformat()
    month_iso = (now - timedelta(days=30)).isoformat()

    total_users = await db.users.count_documents({"deleted": {"$ne": True}})
    paid_users = await db.subscriptions.distinct(
        "userId", {"status": {"$in": ["ACTIVE", "TRIALING"]}})
    paid_count = len(paid_users)

    subs = await db.subscriptions.find(
        {"status": "ACTIVE"}, {"_id": 0}).to_list(2000)
    plan_ids = list({s["planId"] for s in subs if s.get("planId")})
    plans = await db.plans.find(
        {"id": {"$in": plan_ids}}, {"_id": 0}).to_list(50)
    plan_map = {p["id"]: p for p in plans}
    mrr = 0.0
    plan_distribution = {"starter": 0, "growth": 0, "agency": 0}
    for s in subs:
        plan = plan_map.get(s.get("planId"))
        if not plan:
            continue
        mrr += float(plan.get("monthlyPrice", 0))
        name = plan["name"].lower()
        if name in plan_distribution:
            plan_distribution[name] += 1

    churn_month = await db.subscriptions.count_documents({
        "status": "CANCELLED", "updatedAt": {"$gte": month_iso}})
    new_today = await db.users.count_documents({"createdAt": {"$gte": today_iso}})
    new_week = await db.users.count_documents({"createdAt": {"$gte": week_iso}})
    new_month = await db.users.count_documents({"createdAt": {"$gte": month_iso}})

    # Additional real metrics
    articles_today = await db.articles.count_documents({
        "createdAt": {"$gte": today_iso}, "deleted": {"$ne": True}})
    articles_month = await db.articles.count_documents({
        "createdAt": {"$gte": month_iso}, "deleted": {"$ne": True}})
    scans_today = await db.ai_visibility_scans.count_documents({
        "createdAt": {"$gte": today_iso}})
    emails_today = await db.email_logs.count_documents({
        "sentAt": {"$gte": today_iso}}) if "email_logs" in (
        await db.list_collection_names()) else 0

    # Churn as a percentage of cohort active at month start
    active_at_month_start = await db.subscriptions.count_documents({
        "status": {"$in": ["ACTIVE", "TRIALING"]},
        "createdAt": {"$lt": month_iso}})
    churn_pct = (round(churn_month / active_at_month_start * 100, 2)
                 if active_at_month_start else 0.0)

    return ok({
        "totalUsers": total_users,
        "paidUsers": paid_count,
        "freeUsers": total_users - paid_count,
        "MRR": round(mrr, 2),
        "ARR": round(mrr * 12, 2),
        "churnThisMonth": churn_pct,
        "churnCount": churn_month,
        "newSignupsToday": new_today,
        "newSignupsThisWeek": new_week,
        "newSignupsThisMonth": new_month,
        "articlesGeneratedToday": articles_today,
        "articlesGeneratedThisMonth": articles_month,
        "scansRunToday": scans_today,
        "emailsSentToday": emails_today,
        "planDistribution": plan_distribution,
    })


@router.get("/dashboard/activity",
            dependencies=[Depends(get_admin_session)])
async def activity(limit: int = 20):
    db = get_db()
    users = await db.users.find(
        {"deleted": {"$ne": True}},
        {"_id": 0, "password": 0}).sort("createdAt", -1).limit(limit).to_list(limit)
    subs = await db.subscriptions.find(
        {}, {"_id": 0}).sort("createdAt", -1).limit(limit).to_list(limit)
    events = []
    for u in users:
        events.append({"type": "signup", "userId": u["id"],
                       "email": u["email"], "at": u["createdAt"]})
    for s in subs:
        events.append({
            "type": "subscription_" + s.get("status", "?").lower(),
            "userId": s["userId"],
            "planId": s.get("planId"), "at": s.get("createdAt")})
    events.sort(key=lambda x: x.get("at", ""), reverse=True)
    return ok(events[:limit])


# =============================== USERS ====================================

@router.get("/users", dependencies=[Depends(get_admin_session)])
async def list_users(page: int = 1, limit: int = 20,
                     search: Optional[str] = None,
                     plan: Optional[str] = None,
                     status: Optional[str] = None):
    db = get_db()
    q: dict = {"deleted": {"$ne": True}}
    if search:
        q["$or"] = [{"email": {"$regex": search, "$options": "i"}},
                    {"fullName": {"$regex": search, "$options": "i"}}]
    total = await db.users.count_documents(q)
    rows = await db.users.find(q, {"_id": 0, "password": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    # Attach subscription
    for r in rows:
        sub = await db.subscriptions.find_one(
            {"userId": r["id"]}, {"_id": 0}, sort=[("createdAt", -1)])
        r["subscription"] = sub
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/users/{user_id}", dependencies=[Depends(get_admin_session)])
async def user_detail(user_id: str):
    db = get_db()
    user = await db.users.find_one({"id": user_id},
                                   {"_id": 0, "password": 0})
    if not user:
        raise APIError("User not found", "NOT_FOUND", 404)
    sub = await db.subscriptions.find_one(
        {"userId": user_id}, {"_id": 0}, sort=[("createdAt", -1)])
    inv = await db.invoices.find({"userId": user_id}, {"_id": 0}).to_list(100)
    sites = await db.sites.find(
        {"userId": user_id, "deleted": {"$ne": True}},
        {"_id": 0, "apiKey": 0}).to_list(100)
    social = await db.social_accounts.find(
        {"userId": user_id},
        {"_id": 0, "accessToken": 0, "refreshToken": 0}).to_list(100)
    usage = {
        "articles": await db.articles.count_documents(
            {"userId": user_id, "deleted": {"$ne": True}}),
        "socialPosts": await db.social_posts.count_documents({"userId": user_id}),
        "aiScans": await db.ai_visibility_scans.count_documents({"userId": user_id}),
    }
    return ok({"user": user, "subscription": sub, "invoices": inv,
               "sites": sites, "socialAccounts": social, "usage": usage})


@router.delete("/users/{user_id}",
               dependencies=[Depends(get_admin_session)])
async def delete_user(user_id: str, request: Request):
    """Cascade delete: drop the user and every record that belonged to them."""
    db = get_db()
    user = await db.users.find_one({"id": user_id}, {"_id": 0, "password": 0})
    if not user:
        raise APIError("User not found", "NOT_FOUND", 404)

    cascades: dict[str, int] = {}

    async def _purge(name: str, query: dict) -> None:
        res = await db[name].delete_many(query)
        cascades[name] = res.deleted_count

    # Owned collections — keyed by userId
    for coll in ("sites", "articles", "social_posts", "search_terms",
                 "ai_visibility_scans", "competitors", "growth_scores",
                 "article_settings", "brand_voices", "generated_content",
                 "social_accounts", "subscriptions", "invoices",
                 "team_members", "notifications", "user_activity_log",
                 "email_logs"):
        await _purge(coll, {"userId": user_id})

    # Finally the user record itself
    res = await db.users.delete_one({"id": user_id})
    cascades["users"] = res.deleted_count

    # Audit
    await log_action(
        "USER_DELETED", target_type="user", target_id=user_id,
        ip_address=(request.client.host if request.client else ""),
        metadata={"userEmail": user.get("email"),
                  "cascadedDeletes": cascades})

    return ok({
        "deletedUser": user.get("email"),
        "cascadedDeletes": cascades,
    }, "User deleted")


@router.put("/users/{user_id}/plan",
            dependencies=[Depends(get_admin_session)])
async def change_plan(user_id: str, body: PlanChangeReq, request: Request):
    db = get_db()
    plan = await db.plans.find_one({"id": body.planId}, {"_id": 0})
    if not plan:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    sub = await db.subscriptions.find_one(
        {"userId": user_id}, {"_id": 0}, sort=[("createdAt", -1)])
    old_plan_id = (sub or {}).get("planId")
    if sub:
        await db.subscriptions.update_one(
            {"id": sub["id"]},
            {"$set": {"planId": body.planId, "status": "ACTIVE",
                      "updatedAt": utcnow_iso()}})
    else:
        import uuid
        await db.subscriptions.insert_one({
            "id": str(uuid.uuid4()), "userId": user_id,
            "planId": body.planId, "status": "ACTIVE",
            "currentPeriodStart": utcnow_iso(),
            "currentPeriodEnd": (datetime.now(timezone.utc)
                                 + timedelta(days=30)).isoformat(),
            "cancelAtPeriodEnd": False,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        })
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if user:
        await mocks.send_email(user["email"], "plan-changed",
                               f"You've been upgraded to {plan['name']}",
                               f"<p>You're now on the {plan['name']} plan.</p>")
    await log_action(
        "USER_PLAN_CHANGED", target_type="user", target_id=user_id,
        ip_address=(request.client.host if request.client else ""),
        changes={"planId": {"from": old_plan_id, "to": body.planId}})
    return ok({"updated": True})


@router.put("/users/{user_id}/subscription",
            dependencies=[Depends(get_admin_session)])
async def update_subscription(user_id: str, body: SubscriptionUpdateReq,
                              request: Request):
    """Rich admin subscription update — plan + status + trial + interval."""
    import uuid
    db = get_db()
    user = await db.users.find_one({"id": user_id}, {"_id": 0})
    if not user:
        raise APIError("User not found", "NOT_FOUND", 404)

    if body.planId:
        plan = await db.plans.find_one({"id": body.planId}, {"_id": 0})
        if not plan:
            raise APIError("Plan not found", "NOT_FOUND", 404)

    status = (body.status or "").upper() or None
    if status and status not in {"TRIALING", "ACTIVE", "CANCELLED",
                                 "PAST_DUE", "EXPIRED"}:
        raise APIError("Invalid status", "INVALID", 400)

    interval = (body.billingInterval or "").upper() or None
    if interval and interval not in {"MONTHLY", "ANNUAL"}:
        raise APIError("Invalid billingInterval", "INVALID", 400)

    sub = await db.subscriptions.find_one(
        {"userId": user_id}, {"_id": 0}, sort=[("createdAt", -1)])
    now_dt = datetime.now(timezone.utc)
    updates: dict = {"updatedAt": utcnow_iso(), "source": "MANUAL"}
    if body.planId:
        updates["planId"] = body.planId
    if status:
        updates["status"] = status
    if interval:
        updates["billingInterval"] = interval

    if status == "TRIALING" and body.trialDays:
        new_end = (now_dt + timedelta(days=body.trialDays)).isoformat()
        updates["trialEndsAt"] = new_end
        updates["currentPeriodStart"] = now_dt.isoformat()
        updates["currentPeriodEnd"] = new_end
    elif status == "ACTIVE":
        days = 365 if interval == "ANNUAL" else 30
        updates["currentPeriodStart"] = now_dt.isoformat()
        updates["currentPeriodEnd"] = (
            now_dt + timedelta(days=days)).isoformat()
    elif status == "CANCELLED":
        updates["cancelAtPeriodEnd"] = True
        updates["cancelledAt"] = utcnow_iso()
    if body.adminNote:
        updates["adminNote"] = body.adminNote

    if sub:
        await db.subscriptions.update_one(
            {"id": sub["id"]}, {"$set": updates})
        sub_id = sub["id"]
        changes = {k: {"from": sub.get(k), "to": v}
                   for k, v in updates.items() if k != "updatedAt"}
    else:
        sub_id = str(uuid.uuid4())
        doc = {"id": sub_id, "userId": user_id,
               "status": status or "ACTIVE",
               "billingInterval": interval or "MONTHLY",
               "planId": body.planId, "cancelAtPeriodEnd": False,
               "createdAt": utcnow_iso(), **updates}
        await db.subscriptions.insert_one(dict(doc))
        changes = {k: {"from": None, "to": v}
                   for k, v in updates.items() if k != "updatedAt"}

    # Notify the user
    from services import email as _email
    try:
        await _email.announcement_email(
            user["email"], "Your subscription has been updated",
            "<p>Your SEO Jalwa subscription has been updated by our team. "
            "Sign in to view the new plan details.</p>")
    except Exception:
        pass

    # Audit
    await log_action(
        "USER_PLAN_CHANGED" if body.planId else "USER_STATUS_CHANGED",
        target_type="subscription", target_id=user_id,
        ip_address=(request.client.host if request.client else ""),
        changes=changes,
        metadata={"adminNote": body.adminNote or ""})

    fresh = await db.subscriptions.find_one({"id": sub_id}, {"_id": 0})
    plan = (await db.plans.find_one(
        {"id": fresh.get("planId")}, {"_id": 0}) if fresh and fresh.get("planId")
            else None)
    if fresh and plan:
        fresh["plan"] = plan
    return ok({"subscription": fresh}, "Subscription updated")


@router.put("/users/{user_id}/status",
            dependencies=[Depends(get_admin_session)])
async def change_status(user_id: str, body: StatusReq):
    if body.status not in {"active", "suspended"}:
        raise APIError("Invalid status", "INVALID", 400)
    await get_db().users.update_one(
        {"id": user_id},
        {"$set": {"status": body.status, "updatedAt": utcnow_iso()}})
    return ok({"updated": True})


@router.post("/users/{user_id}/extend-trial",
             dependencies=[Depends(get_admin_session)])
async def extend_trial(user_id: str, body: TrialReq):
    db = get_db()
    sub = await db.subscriptions.find_one(
        {"userId": user_id}, {"_id": 0}, sort=[("createdAt", -1)])
    if not sub:
        raise APIError("No subscription", "NOT_FOUND", 404)
    current = sub.get("trialEndsAt")
    base = (datetime.fromisoformat(current)
            if current else datetime.now(timezone.utc))
    new_end = (base + timedelta(days=body.days)).isoformat()
    await db.subscriptions.update_one(
        {"id": sub["id"]},
        {"$set": {"trialEndsAt": new_end, "status": "TRIALING",
                  "updatedAt": utcnow_iso()}})
    return ok({"trialEndsAt": new_end})


@router.post("/users/{user_id}/note",
             dependencies=[Depends(get_admin_session)])
async def add_note(user_id: str, body: NoteReq):
    import uuid
    await get_db().admin_notes.insert_one({
        "id": str(uuid.uuid4()), "userId": user_id,
        "note": body.note, "createdAt": utcnow_iso(),
    })
    return ok({"saved": True})


@router.get("/users/{user_id}/activity",
            dependencies=[Depends(get_admin_session)])
async def user_activity(user_id: str, page: int = 1, limit: int = 20):
    db = get_db()
    events = []
    async for a in db.articles.find(
            {"userId": user_id}, {"_id": 0, "content": 0}).sort(
            "createdAt", -1).limit(50):
        events.append({"type": "article_created", "at": a["createdAt"],
                       "ref": a["id"], "title": a.get("title")})
    async for s in db.social_posts.find(
            {"userId": user_id}, {"_id": 0}).sort("createdAt", -1).limit(50):
        events.append({"type": "social_post", "at": s["createdAt"],
                       "ref": s["id"], "platform": s.get("platform")})
    events.sort(key=lambda x: x["at"], reverse=True)
    start = (page - 1) * limit
    return ok(events[start:start + limit],
              pagination=paginate([], len(events), page, limit))
