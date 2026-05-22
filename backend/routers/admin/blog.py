"""Admin: blog CMS — Phase 3 Part 4 rich editor backend."""
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso

router = APIRouter(prefix="/admin/blog", tags=["admin-blog"],
                   dependencies=[Depends(get_admin_session)])


class PostReq(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    excerpt: Optional[str] = None
    featuredImageUrl: Optional[str] = None
    featuredImageAlt: Optional[str] = None
    seoMetaTitle: Optional[str] = None
    seoMetaDescription: Optional[str] = None
    # legacy aliases
    metaTitle: Optional[str] = None
    metaDescription: Optional[str] = None
    tags: Optional[list] = None
    category: Optional[str] = None
    author: Optional[str] = None
    status: Optional[str] = None  # DRAFT | PUBLISHED | SCHEDULED
    scheduledAt: Optional[str] = None
    publishedAt: Optional[str] = None


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:100] or uuid.uuid4().hex[:8]


async def _unique_slug(db, base: str, *, exclude_id: str | None = None) -> str:
    """Return a slug that doesn't collide. Appends -2, -3, ... as needed."""
    slug = base
    n = 2
    while True:
        q = {"slug": slug}
        if exclude_id:
            q["id"] = {"$ne": exclude_id}
        if not await db.blog_posts.find_one(q, {"_id": 0, "id": 1}):
            return slug
        slug = f"{base}-{n}"
        n += 1


def _read_time(content_html: str) -> int:
    """Return reading time in minutes (200 words/min)."""
    text = re.sub(r"<[^<]+?>", " ", content_html or "")
    word_count = len(text.split())
    return max(1, round(word_count / 200))


@router.get("")
async def list_posts(page: int = 1, limit: int = 20,
                     status: Optional[str] = None):
    q: dict = {}
    if status:
        q["status"] = status.upper()
    db = get_db()
    total = await db.blog_posts.count_documents(q)
    rows = await db.blog_posts.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/{post_id}")
async def get_post(post_id: str):
    p = await get_db().blog_posts.find_one({"id": post_id}, {"_id": 0})
    if not p:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok(p)


@router.post("")
async def create_post(body: PostReq):
    if not body.title or not body.content:
        raise APIError("title and content required", "INVALID", 400)
    db = get_db()
    post_id = str(uuid.uuid4())
    slug = await _unique_slug(db, _slug(body.title))
    seo_title = body.seoMetaTitle or body.metaTitle or body.title
    seo_desc = body.seoMetaDescription or body.metaDescription or ""
    status = (body.status or "DRAFT").upper()
    doc = {
        "id": post_id,
        "title": body.title, "slug": slug,
        "content": body.content,
        "excerpt": body.excerpt or "",
        "featuredImageUrl": body.featuredImageUrl or "",
        "featuredImageAlt": body.featuredImageAlt or "",
        "seoMetaTitle": seo_title,
        "seoMetaDescription": seo_desc,
        "metaTitle": seo_title,            # legacy
        "metaDescription": seo_desc,       # legacy
        "tags": body.tags or [],
        "category": body.category or "",
        "author": body.author or "SEO Jalwa Team",
        "readTime": _read_time(body.content),
        "status": status,
        "scheduledAt": body.scheduledAt,
        "publishedAt": (body.publishedAt or utcnow_iso()
                         if status == "PUBLISHED" else None),
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await db.blog_posts.insert_one(dict(doc))
    doc.pop("_id", None)
    return created(doc)


@router.put("/{post_id}")
async def update_post(post_id: str, body: PostReq):
    db = get_db()
    existing = await db.blog_posts.find_one({"id": post_id}, {"_id": 0})
    if not existing:
        raise APIError("Post not found", "NOT_FOUND", 404)
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "title" in upd:
        base = _slug(upd["title"])
        upd["slug"] = await _unique_slug(db, base, exclude_id=post_id)
    if "content" in upd:
        upd["readTime"] = _read_time(upd["content"])
    if "status" in upd:
        upd["status"] = upd["status"].upper()
        if (upd["status"] == "PUBLISHED"
                and not existing.get("publishedAt")):
            upd["publishedAt"] = utcnow_iso()
    # Mirror legacy keys
    if "seoMetaTitle" in upd:
        upd["metaTitle"] = upd["seoMetaTitle"]
    if "seoMetaDescription" in upd:
        upd["metaDescription"] = upd["seoMetaDescription"]
    upd["updatedAt"] = utcnow_iso()
    await db.blog_posts.update_one({"id": post_id}, {"$set": upd})
    fresh = await db.blog_posts.find_one({"id": post_id}, {"_id": 0})
    return ok({"updated": True, "post": fresh})


@router.delete("/{post_id}")
async def delete_post(post_id: str):
    res = await get_db().blog_posts.delete_one({"id": post_id})
    if res.deleted_count == 0:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok({"deleted": True})


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...),
                        postId: Optional[str] = None):
    """Upload an editor image to R2. Used inline by the rich text editor."""
    contents = await file.read()
    if not contents:
        raise APIError("Empty file", "EMPTY_FILE", 400)
    if len(contents) > 10 * 1024 * 1024:
        raise APIError("File too large (max 10MB)", "FILE_TOO_LARGE", 400)

    from services import storage
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-",
                        file.filename or "image.jpg")
    folder = postId or "drafts"
    key = f"blog/{folder}/images/{uuid.uuid4().hex[:8]}-{safe_name}"
    url = await storage.upload_file(
        contents, key, content_type=file.content_type or "image/jpeg")
    return ok({"url": url, "key": key,
                "size": len(contents),
                "contentType": file.content_type or "image/jpeg"})
