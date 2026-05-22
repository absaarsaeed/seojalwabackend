"""User profile / settings / account deletion."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import hash_password, utcnow_iso, verify_password
from routers.sites import clean_website_url

router = APIRouter(prefix="/user", tags=["user"])


class ProfileReq(BaseModel):
    fullName: Optional[str] = None
    email: Optional[EmailStr] = None
    websiteUrl: Optional[str] = None


class PasswordReq(BaseModel):
    currentPassword: str
    newPassword: str = Field(min_length=8)


class NotificationsReq(BaseModel):
    emailDigest: Optional[bool] = None
    weeklyScore: Optional[bool] = None
    aiAlerts: Optional[bool] = None
    billingAlerts: Optional[bool] = None


class DeleteReq(BaseModel):
    password: str


# ============================ ONBOARDING =================================
DEFAULT_ONBOARDING = {
    "websiteConnected": False,
    "articleSettingsConfigured": False,
    "searchTermsAdded": False,
    "firstScanRun": False,
    "dismissed": False,
}

_ONB_STEPS = set(DEFAULT_ONBOARDING) - {"dismissed"}


async def _compute_onboarding(user_id: str) -> dict:
    """Read user.onboarding and merge with auto-detected steps from data.

    Steps auto-flip to True as soon as the relevant DB rows exist:
      - websiteConnected → any user site has wordpressConnected=true
      - articleSettingsConfigured → any article_settings doc for user's sites
      - searchTermsAdded → any search_terms doc for user's sites
      - firstScanRun → any ai_visibility_scan for user

    `dismissed` is set only by an explicit PUT, never inferred.
    """
    db = get_db()
    user_doc = await db.users.find_one(
        {"id": user_id}, {"_id": 0, "onboarding": 1}) or {}
    stored = (user_doc.get("onboarding") or {}).copy()

    sites = await db.sites.find(
        {"userId": user_id, "deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "wordpressConnected": 1}).to_list(50)
    site_ids = [s["id"] for s in sites]

    auto = {
        "websiteConnected": any(s.get("wordpressConnected") for s in sites),
        "articleSettingsConfigured": (
            await db.article_settings.count_documents(
                {"siteId": {"$in": site_ids}})) > 0
            if site_ids else False,
        "searchTermsAdded": (await db.search_terms.count_documents(
            {"siteId": {"$in": site_ids}})) > 0 if site_ids else False,
        "firstScanRun": (await db.ai_visibility_scans.count_documents(
            {"userId": user_id})) > 0,
    }
    merged = {**DEFAULT_ONBOARDING, **stored}
    for k, v in auto.items():
        if v:
            merged[k] = True
    # Persist auto-flips so we don't recompute every refresh
    if merged != stored:
        await db.users.update_one(
            {"id": user_id}, {"$set": {"onboarding": merged,
                                        "updatedAt": utcnow_iso()}})
    merged["completed"] = all(merged.get(s) for s in _ONB_STEPS)
    return merged


class OnboardingReq(BaseModel):
    step: Optional[str] = None
    completed: Optional[bool] = None
    dismissed: Optional[bool] = None


@router.get("/onboarding")
async def get_onboarding(user=Depends(get_current_user)):
    return ok({"onboarding": await _compute_onboarding(user["id"])})


@router.put("/onboarding")
async def update_onboarding(body: OnboardingReq,
                            user=Depends(get_current_user)):
    db = get_db()
    current = await _compute_onboarding(user["id"])
    if body.dismissed is not None:
        current["dismissed"] = bool(body.dismissed)
    if body.step:
        if body.step not in _ONB_STEPS:
            raise APIError("Unknown onboarding step", "INVALID_STEP", 400)
        current[body.step] = bool(body.completed
                                   if body.completed is not None else True)
    current.pop("completed", None)  # computed, never stored
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"onboarding": current,
                  "updatedAt": utcnow_iso()}})
    return ok({"onboarding": await _compute_onboarding(user["id"])})


# ============================ PROFILE ====================================


@router.put("/profile")
async def update_profile(body: ProfileReq, user=Depends(get_current_user)):
    db = get_db()
    upd: dict = {}

    if body.fullName is not None:
        upd["fullName"] = body.fullName

    if body.email is not None and body.email.lower() != user.get("email"):
        new_email = body.email.lower()
        taken = await db.users.find_one(
            {"email": new_email, "id": {"$ne": user["id"]}})
        if taken:
            raise APIError("Email already in use", "EMAIL_TAKEN", 409)
        upd["email"] = new_email
        # FIX 9: keep emailVerified=True on email change (verification disabled)
        upd["emailVerified"] = True

    old_url = (user.get("websiteUrl") or "").strip()
    if body.websiteUrl is not None:
        new_url = clean_website_url(body.websiteUrl)
        upd["websiteUrl"] = new_url
        if new_url and new_url != old_url:
            # Propagate change to the matching existing Site, if any
            await db.sites.update_one(
                {"userId": user["id"], "url": old_url,
                 "deleted": {"$ne": True}},
                {"$set": {"url": new_url, "updatedAt": utcnow_iso()}})

    if upd:
        upd["updatedAt"] = utcnow_iso()
        await db.users.update_one({"id": user["id"]}, {"$set": upd})

    fresh = await db.users.find_one(
        {"id": user["id"]}, {"_id": 0, "password": 0})
    return ok({"user": fresh, "updated": True}, "Profile updated")


@router.put("/password")
async def update_password(body: PasswordReq, user=Depends(get_current_user)):
    full = await get_db().users.find_one({"id": user["id"]})
    if not full or not verify_password(body.currentPassword,
                                       full.get("password", "")):
        raise APIError("Current password is incorrect",
                       "INVALID_CREDENTIALS", 401)
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"password": hash_password(body.newPassword),
                  "updatedAt": utcnow_iso()}})
    return ok({"updated": True}, "Password updated")


@router.put("/notifications")
async def update_notifications(body: NotificationsReq,
                               user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if upd:
        await get_db().users.update_one(
            {"id": user["id"]}, {"$set": {"notifications": upd,
                                          "updatedAt": utcnow_iso()}})
    return ok({"updated": True})


@router.delete("/account")
async def delete_account(body: DeleteReq, user=Depends(get_current_user)):
    full = await get_db().users.find_one({"id": user["id"]})
    if not full or not verify_password(body.password, full.get("password", "")):
        raise APIError("Password incorrect", "INVALID_CREDENTIALS", 401)
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"deleted": True, "updatedAt": utcnow_iso()}})
    await get_db().subscriptions.update_many(
        {"userId": user["id"], "status": "ACTIVE"},
        {"$set": {"status": "CANCELLED", "cancelAtPeriodEnd": True}})
    return ok({"deleted": True})


# ============================= QUOTA =====================================
# Phase 2 Part 3 — shared article quota with per-site allocation.

class SiteQuotaReq(BaseModel):
    quotaPerMonth: int


class DismissPluginReq(BaseModel):
    version: str


@router.put("/dismiss-plugin-banner")
async def dismiss_plugin_banner(body: DismissPluginReq,
                                 user=Depends(get_current_user)):
    await get_db().users.update_one(
        {"id": user["id"]},
        {"$set": {"dismissedPluginVersion": body.version,
                  "updatedAt": utcnow_iso()}})
    return ok({"dismissed": True, "version": body.version})


def _month_start_iso() -> str:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0,
                       microsecond=0).isoformat()


async def _plan_total_articles(user_id: str) -> tuple[int, dict | None]:
    db = get_db()
    sub = await db.subscriptions.find_one(
        {"userId": user_id, "status": {"$in": ["ACTIVE", "TRIALING"]}},
        {"_id": 0}, sort=[("createdAt", -1)])
    if not sub or not sub.get("planId"):
        return 0, None
    plan = await db.plans.find_one({"id": sub["planId"]}, {"_id": 0})
    if not plan:
        return 0, None
    feats = (plan.get("features") or {}).get("articlesPerMonth") or {}
    total = (int(feats.get("value", 0) or 0)
             if feats else int(plan.get("articlesPerMonth", 0) or 0))
    return total, plan


@router.get("/quota")
async def get_quota(user=Depends(get_current_user)):
    db = get_db()
    plan_total, plan = await _plan_total_articles(user["id"])
    sites = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0, "id": 1, "name": 1, "url": 1}).to_list(50)
    site_ids = [s["id"] for s in sites]
    month_start = _month_start_iso()

    # Per-site usage
    if site_ids:
        agg = await db.articles.aggregate([
            {"$match": {"userId": user["id"],
                         "siteId": {"$in": site_ids},
                         "deleted": {"$ne": True},
                         "createdAt": {"$gte": month_start}}},
            {"$group": {"_id": "$siteId", "n": {"$sum": 1}}},
        ]).to_list(50)
    else:
        agg = []
    used_by_site = {r["_id"]: r["n"] for r in agg}

    # Per-site stored quota allocations (default: equal split, rounded up)
    quotas = await db.site_article_quotas.find(
        {"userId": user["id"]}, {"_id": 0}).to_list(50)
    quota_map = {q["siteId"]: q for q in quotas}

    auto_default = (plan_total // len(sites)) if sites and plan_total else 0
    # Distribute remainder to the first sites
    remainder = plan_total - auto_default * len(sites) if sites else 0

    out_sites = []
    used_total = 0
    for i, s in enumerate(sites):
        q = quota_map.get(s["id"])
        allocated = (q.get("quotaPerMonth") if q
                     else (auto_default + (1 if i < remainder else 0)))
        used = used_by_site.get(s["id"], 0)
        used_total += used
        out_sites.append({
            "siteId": s["id"], "siteName": s.get("name"),
            "url": s.get("url"),
            "quotaAllocated": allocated,
            "usedThisMonth": used,
            "remaining": max(0, allocated - used),
            "autoDistribute": not q,
        })

    return ok({
        "planTotal": plan_total,
        "planName": (plan or {}).get("name"),
        "usedThisMonth": used_total,
        "remainingTotal": max(0, plan_total - used_total),
        "sites": out_sites,
    })


@router.put("/quota/sites/{site_id}")
async def set_site_quota(site_id: str, body: SiteQuotaReq,
                          user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one(
        {"id": site_id, "userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    if body.quotaPerMonth < 0:
        raise APIError("Quota must be >= 0", "INVALID_QUOTA", 400)

    plan_total, _ = await _plan_total_articles(user["id"])

    # Sum of OTHER sites' quotas (defaulting to even split when unset)
    sites = await db.sites.find(
        {"userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0, "id": 1}).to_list(50)
    other_ids = [s["id"] for s in sites if s["id"] != site_id]
    quotas = await db.site_article_quotas.find(
        {"userId": user["id"], "siteId": {"$in": other_ids}},
        {"_id": 0}).to_list(50)
    quota_map = {q["siteId"]: q["quotaPerMonth"] for q in quotas}
    auto_default = (plan_total // len(sites)) if sites and plan_total else 0
    sum_others = sum(quota_map.get(sid, auto_default) for sid in other_ids)

    if body.quotaPerMonth + sum_others > plan_total:
        raise APIError(
            (f"Quota total exceeds plan limit. Plan allows {plan_total}, "
             f"other sites already use {sum_others}."),
            "QUOTA_EXCEEDS_PLAN", 400,
            meta={"planTotal": plan_total, "sumOthers": sum_others})

    import uuid as _uuid
    existing = await db.site_article_quotas.find_one(
        {"userId": user["id"], "siteId": site_id}, {"_id": 0})
    if existing:
        await db.site_article_quotas.update_one(
            {"id": existing["id"]},
            {"$set": {"quotaPerMonth": body.quotaPerMonth,
                       "autoDistribute": False,
                       "updatedAt": utcnow_iso()}})
    else:
        await db.site_article_quotas.insert_one({
            "id": str(_uuid.uuid4()),
            "userId": user["id"], "siteId": site_id,
            "quotaPerMonth": body.quotaPerMonth,
            "autoDistribute": False,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        })
    return ok({"updated": True, "quotaPerMonth": body.quotaPerMonth})
