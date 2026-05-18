"""Social accounts + Social Autopilot (posts) routes."""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.encryption import encrypt
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso
from services import jobs, mocks

router = APIRouter(prefix="/social", tags=["social"])

PLATFORMS = {"INSTAGRAM", "FACEBOOK", "LINKEDIN", "TWITTER", "PINTEREST",
             "YOUTUBE"}


# ============================ SOCIAL ACCOUNTS ===============================

@router.get("/accounts")
async def list_accounts(user=Depends(get_current_user)):
    db = get_db()
    accounts = await db.social_accounts.find(
        {"userId": user["id"]},
        {"_id": 0, "accessToken": 0, "refreshToken": 0}).to_list(100)
    return ok(accounts)


@router.get("/auth/{platform}")
async def auth_url(platform: str, user=Depends(get_current_user)):
    plat = platform.upper()
    if plat not in PLATFORMS:
        raise APIError("Unsupported platform", "INVALID_PLATFORM", 400)
    state = f"{user['id']}:{uuid.uuid4().hex}"
    redirect = f"{os.environ.get('FRONTEND_URL', '')}/api/social/callback/{platform}"
    url = await mocks.get_social_oauth_url(platform.lower(), redirect, state)
    return ok({"authUrl": url, "state": state})


@router.get("/callback/{platform}")
async def auth_callback(platform: str, code: str = Query(...),
                        state: str = Query(...)):
    plat = platform.upper()
    if plat not in PLATFORMS:
        raise APIError("Unsupported platform", "INVALID_PLATFORM", 400)
    try:
        user_id = state.split(":")[0]
    except Exception:
        raise APIError("Invalid state", "INVALID_STATE", 400)
    tokens = await mocks.social_exchange_code(platform.lower(), code)
    account = {
        "id": str(uuid.uuid4()), "userId": user_id, "siteId": None,
        "platform": plat,
        "accessToken": encrypt(tokens["access_token"]),
        "refreshToken": encrypt(tokens.get("refresh_token")),
        "tokenExpiry": (datetime.now(timezone.utc).timestamp()
                        + tokens.get("expires_in", 3600)),
        "accountName": tokens.get("account_name"),
        "accountId": tokens.get("account_id"),
        "followerCount": tokens.get("follower_count", 0),
        "isActive": True,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await get_db().social_accounts.insert_one(dict(account))
    frontend = os.environ.get("FRONTEND_URL", "/")
    return RedirectResponse(url=f"{frontend}/dashboard/connections")


@router.delete("/accounts/{account_id}")
async def disconnect_account(account_id: str, user=Depends(get_current_user)):
    res = await get_db().social_accounts.delete_one(
        {"id": account_id, "userId": user["id"]})
    if res.deleted_count == 0:
        raise APIError("Account not found", "NOT_FOUND", 404)
    return ok({"disconnected": True})


# ============================ SOCIAL POSTS ==================================

class PostCreate(BaseModel):
    siteId: str
    platforms: list[str]
    caption: str
    imageUrl: Optional[str] = None
    scheduledAt: Optional[str] = None
    hashtags: list[str] = []


class PostUpdate(BaseModel):
    caption: Optional[str] = None
    scheduledAt: Optional[str] = None
    status: Optional[str] = None
    imageUrl: Optional[str] = None


class GenerateFromArticle(BaseModel):
    articleId: str
    platforms: list[str] = []


@router.get("/posts")
async def list_posts(
    user=Depends(get_current_user),
    siteId: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1, limit: int = 20,
):
    db = get_db()
    q: dict = {"userId": user["id"]}
    if siteId:
        q["siteId"] = siteId
    if platform:
        q["platform"] = platform.upper()
    if status:
        q["status"] = status.upper()
    total = await db.social_posts.count_documents(q)
    rows = await db.social_posts.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.post("/posts")
async def create_post(body: PostCreate, user=Depends(get_current_user)):
    created_ids = []
    for plat in body.platforms:
        post = {
            "id": str(uuid.uuid4()), "userId": user["id"],
            "siteId": body.siteId, "articleId": None,
            "platform": plat.upper(), "caption": body.caption,
            "imageUrl": body.imageUrl, "hashtags": body.hashtags,
            "status": "SCHEDULED" if body.scheduledAt else "DRAFT",
            "scheduledAt": body.scheduledAt, "publishedAt": None,
            "platformPostId": None, "reach": 0, "likes": 0, "clicks": 0,
            "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
        }
        await get_db().social_posts.insert_one(dict(post))
        created_ids.append(post["id"])
    return created({"postIds": created_ids}, "Posts created")


@router.post("/posts/generate")
async def generate_from_article(body: GenerateFromArticle,
                                bg: BackgroundTasks,
                                user=Depends(get_current_user)):
    db = get_db()
    article = await db.articles.find_one(
        {"id": body.articleId, "userId": user["id"]}, {"_id": 0})
    if not article:
        raise APIError("Article not found", "NOT_FOUND", 404)
    job_id = await jobs.create_job("social_post_generation", {
        "articleId": body.articleId})
    bg.add_task(jobs.run_social_post_generation, job_id, body.articleId,
                user["id"], article["siteId"], body.platforms or None)
    return ok({"jobId": job_id, "status": "queued"})


@router.put("/posts/{post_id}")
async def update_post(post_id: str, body: PostUpdate,
                      user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    upd["updatedAt"] = utcnow_iso()
    res = await get_db().social_posts.update_one(
        {"id": post_id, "userId": user["id"]}, {"$set": upd})
    if res.matched_count == 0:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok({"updated": True})


@router.delete("/posts/{post_id}")
async def delete_post(post_id: str, user=Depends(get_current_user)):
    res = await get_db().social_posts.delete_one(
        {"id": post_id, "userId": user["id"]})
    if res.deleted_count == 0:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok({"deleted": True})


@router.post("/posts/{post_id}/approve")
async def approve_post(post_id: str, user=Depends(get_current_user)):
    res = await get_db().social_posts.update_one(
        {"id": post_id, "userId": user["id"], "status": "PENDING_APPROVAL"},
        {"$set": {"status": "SCHEDULED", "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Post not pending approval", "INVALID_STATE", 400)
    return ok({"approved": True})


@router.post("/posts/{post_id}/publish-now")
async def publish_now(post_id: str, user=Depends(get_current_user)):
    db = get_db()
    post = await db.social_posts.find_one(
        {"id": post_id, "userId": user["id"]}, {"_id": 0})
    if not post:
        raise APIError("Post not found", "NOT_FOUND", 404)
    account = await db.social_accounts.find_one(
        {"userId": user["id"], "platform": post["platform"], "isActive": True},
        {"_id": 0})
    if not account:
        raise APIError("Connect this social account first",
                       "NO_ACCOUNT", 400)
    res = await mocks.publish_social_post(post["platform"], account, post)
    await db.social_posts.update_one(
        {"id": post_id},
        {"$set": {"status": "PUBLISHED", "publishedAt": utcnow_iso(),
                  "platformPostId": res["platformPostId"]}})
    return ok({"published": True, "platformPostId": res["platformPostId"]})


@router.get("/analytics")
async def social_analytics(user=Depends(get_current_user),
                           siteId: Optional[str] = None,
                           platform: Optional[str] = None,
                           dateRange: Optional[str] = "30d"):
    db = get_db()
    q: dict = {"userId": user["id"], "status": "PUBLISHED"}
    if siteId:
        q["siteId"] = siteId
    if platform:
        q["platform"] = platform.upper()
    rows = await db.social_posts.find(q, {"_id": 0}).to_list(1000)
    total_reach = sum(r.get("reach", 0) for r in rows)
    total_likes = sum(r.get("likes", 0) for r in rows)
    total_clicks = sum(r.get("clicks", 0) for r in rows)
    return ok({
        "totalPosts": len(rows), "totalReach": total_reach,
        "totalLikes": total_likes, "totalClicks": total_clicks,
        "dateRange": dateRange,
    })
