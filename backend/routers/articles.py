"""Articles routes — list, generate, update, publish, calendar."""
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.plan_limits import check_article_limit
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso
from services import jobs, mocks

router = APIRouter(prefix="/articles", tags=["articles"])

CMS_CONNECTION_FIELD = {
    "wordpress": "wordpressConnected", "webflow": "webflowToken",
    "ghost": "ghostApiKey", "hubspot": "hubspotToken",
    "wix": "wixApiKey", "notion": "notionToken",
}


class GenerateReq(BaseModel):
    siteId: str
    searchTerm: str
    settingsOverride: Optional[dict] = None


class UpdateReq(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    metaTitle: Optional[str] = None
    metaDescription: Optional[str] = None
    excerpt: Optional[str] = None
    scheduledAt: Optional[str] = None
    status: Optional[str] = None


class PublishReq(BaseModel):
    destination: str
    siteId: Optional[str] = None


class RescheduleReq(BaseModel):
    scheduledAt: str


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:100] or uuid.uuid4().hex[:8]


@router.get("")
async def list_articles(
    user=Depends(get_current_user),
    siteId: Optional[str] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    page: int = 1, limit: int = 20,
):
    db = get_db()
    q: dict = {"userId": user["id"], "deleted": {"$ne": True}}
    if siteId:
        q["siteId"] = siteId
    if status:
        q["status"] = status.upper()
    if search:
        q["title"] = {"$regex": search, "$options": "i"}
    total = await db.articles.count_documents(q)
    rows = await db.articles.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/calendar")
async def calendar(siteId: str, year: int, month: int,
                   user=Depends(get_current_user)):
    """Phase 3 Part 3 — enriched calendar.

    Articles are grouped by either `publishedAt` (PUBLISHED) or
    `scheduledAt` (SCHEDULED/QUEUED). Each entry carries the title (or
    search term fallback), status, seoScore, wordCount, both timestamps,
    featuredImageUrl, the live CMS URL, and a short excerpt.
    """
    db = get_db()
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end_year = year + (1 if month == 12 else 0)
    end_month = 1 if month == 12 else month + 1
    end = datetime(end_year, end_month, 1, tzinfo=timezone.utc)
    start_iso = start.isoformat()
    end_iso = end.isoformat()

    rows = await db.articles.find({
        "userId": user["id"], "siteId": siteId,
        "deleted": {"$ne": True},
        "$or": [
            {"scheduledAt": {"$gte": start_iso, "$lt": end_iso}},
            {"publishedAt": {"$gte": start_iso, "$lt": end_iso}},
        ],
    }, {"_id": 0}).to_list(2000)

    grouped: dict[str, list] = {}
    for r in rows:
        published_at = r.get("publishedAt")
        scheduled_at = r.get("scheduledAt")
        # Bucket by published date if available, else scheduled date
        bucket = (published_at or scheduled_at or "")[:10]
        if not bucket:
            continue
        status = r.get("status", "DRAFT")
        title = (r.get("title")
                 if r.get("title") and r.get("title") != r.get("searchTerm")
                 else (r.get("title") or r.get("searchTerm") or "Untitled"))
        # Excerpt — first 120 chars stripped of HTML
        excerpt = r.get("excerpt") or ""
        if not excerpt and r.get("content"):
            text = re.sub(r"<[^<]+?>", "", r.get("content", ""))
            excerpt = text[:120].strip()
        entry = {
            "id": r.get("id"),
            "title": title,
            "searchTerm": r.get("searchTerm", ""),
            "status": status,
            "seoScore": r.get("seoScore", 0),
            "wordCount": r.get("wordCount", 0),
            "publishedAt": published_at,
            "scheduledAt": scheduled_at,
            "featuredImageUrl": r.get("featuredImageUrl", ""),
            "cmsUrl": r.get("cmsUrl", ""),
            "excerpt": excerpt,
        }
        grouped.setdefault(bucket, []).append(entry)
    return ok(grouped)


@router.post("/generate")
async def generate(body: GenerateReq, bg: BackgroundTasks,
                   user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one({"id": body.siteId, "userId": user["id"]},
                                   {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    # Enforce monthly article quota from the user's plan
    usage = await check_article_limit(user["id"])
    article_id = str(uuid.uuid4())
    await db.articles.insert_one({
        "id": article_id, "siteId": body.siteId, "userId": user["id"],
        "title": body.searchTerm, "slug": _slugify(body.searchTerm),
        "content": "", "searchTerm": body.searchTerm,
        "status": "DRAFT", "wordCount": 0,
        "impressions": 0, "clicks": 0,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    })
    job_id = await jobs.create_job("article_generation",
                                   {"articleId": article_id})
    bg.add_task(jobs.run_article_generation, job_id, article_id,
                body.siteId, user["id"], body.searchTerm)
    return ok({"jobId": job_id, "articleId": article_id,
               "status": "queued", "quota": usage})


@router.get("/job/{job_id}")
async def job_status(job_id: str, user=Depends(get_current_user)):
    job = await get_db().jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise APIError("Job not found", "NOT_FOUND", 404)
    return ok(job)


@router.get("/{article_id}")
async def get_article(article_id: str, user=Depends(get_current_user)):
    article = await get_db().articles.find_one(
        {"id": article_id, "userId": user["id"], "deleted": {"$ne": True}},
        {"_id": 0})
    if not article:
        raise APIError("Article not found", "NOT_FOUND", 404)
    return ok(article)


@router.put("/{article_id}")
async def update_article(article_id: str, body: UpdateReq,
                         user=Depends(get_current_user)):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "title" in upd and upd["title"]:
        upd["slug"] = _slugify(upd["title"])
    if "content" in upd and upd["content"]:
        upd["wordCount"] = len(upd["content"].split())
    upd["updatedAt"] = utcnow_iso()
    res = await get_db().articles.update_one(
        {"id": article_id, "userId": user["id"]}, {"$set": upd})
    if res.matched_count == 0:
        raise APIError("Article not found", "NOT_FOUND", 404)
    return ok({"updated": True})


@router.post("/{article_id}/publish")
async def publish_article(article_id: str, body: PublishReq,
                          bg: BackgroundTasks,
                          user=Depends(get_current_user)):
    db = get_db()
    article = await db.articles.find_one(
        {"id": article_id, "userId": user["id"]}, {"_id": 0})
    if not article:
        raise APIError("Article not found", "NOT_FOUND", 404)
    site = await db.sites.find_one({"id": article["siteId"]}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    res = await mocks.publish_to_cms(body.destination.lower(), site, article)
    await db.articles.update_one({"id": article_id}, {"$set": {
        "status": "PUBLISHED", "publishedAt": utcnow_iso(),
        "cmsPostId": res["cmsPostId"], "cmsUrl": res["cmsUrl"],
        "updatedAt": utcnow_iso(),
    }})
    # Trigger social post generation
    job_id = await jobs.create_job("social_post_generation",
                                   {"articleId": article_id})
    bg.add_task(jobs.run_social_post_generation, job_id, article_id,
                user["id"], article["siteId"], None)
    return ok({"success": True, "url": res["cmsUrl"]})


@router.delete("/{article_id}")
async def delete_article(article_id: str, user=Depends(get_current_user)):
    res = await get_db().articles.update_one(
        {"id": article_id, "userId": user["id"]},
        {"$set": {"deleted": True, "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Article not found", "NOT_FOUND", 404)
    return ok({"deleted": True})


@router.post("/{article_id}/reschedule")
async def reschedule(article_id: str, body: RescheduleReq,
                     user=Depends(get_current_user)):
    res = await get_db().articles.update_one(
        {"id": article_id, "userId": user["id"]},
        {"$set": {"scheduledAt": body.scheduledAt,
                  "status": "SCHEDULED",
                  "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Article not found", "NOT_FOUND", 404)
    return ok({"rescheduled": True})


@router.post("/{article_id}/retry")
async def retry_article(article_id: str, bg: BackgroundTasks,
                        user=Depends(get_current_user)):
    """Re-queue a FAILED article for generation."""
    db = get_db()
    art = await db.articles.find_one(
        {"id": article_id, "userId": user["id"]}, {"_id": 0})
    if not art:
        raise APIError("Article not found", "NOT_FOUND", 404)
    if art.get("status") not in {"FAILED", "DRAFT"}:
        raise APIError("Only FAILED articles can be retried",
                       "INVALID_STATE", 400)
    # Enforce plan quota again on retry
    usage = await check_article_limit(user["id"])
    job_id = str(uuid.uuid4())
    await db.jobs.insert_one({
        "id": job_id, "type": "article-generation", "status": "queued",
        "progress": 0, "createdAt": utcnow_iso()})
    await db.articles.update_one(
        {"id": article_id},
        {"$set": {"status": "GENERATING", "updatedAt": utcnow_iso()}})
    bg.add_task(jobs.run_article_generation, job_id, article_id,
                art["siteId"], user["id"],
                art.get("searchTerm") or art.get("title") or "")
    return ok({"jobId": job_id, "articleId": article_id,
               "status": "queued", "quota": usage}, "Retry queued")
