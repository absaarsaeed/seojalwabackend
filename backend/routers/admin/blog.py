"""Admin: blog CMS."""
import re
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
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
    metaTitle: Optional[str] = None
    metaDescription: Optional[str] = None
    status: Optional[str] = None  # DRAFT | PUBLISHED
    publishedAt: Optional[str] = None


def _slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:100] or uuid.uuid4().hex[:8]


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
    doc = {
        "id": str(uuid.uuid4()), "title": body.title,
        "slug": _slug(body.title), "content": body.content,
        "excerpt": body.excerpt, "featuredImageUrl": body.featuredImageUrl,
        "metaTitle": body.metaTitle,
        "metaDescription": body.metaDescription,
        "status": (body.status or "DRAFT").upper(),
        "publishedAt": body.publishedAt,
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await get_db().blog_posts.insert_one(dict(doc))
    doc.pop("_id", None)
    return created(doc)


@router.put("/{post_id}")
async def update_post(post_id: str, body: PostReq):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "title" in upd:
        upd["slug"] = _slug(upd["title"])
    if "status" in upd:
        upd["status"] = upd["status"].upper()
    upd["updatedAt"] = utcnow_iso()
    res = await get_db().blog_posts.update_one(
        {"id": post_id}, {"$set": upd})
    if res.matched_count == 0:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok({"updated": True})


@router.delete("/{post_id}")
async def delete_post(post_id: str):
    res = await get_db().blog_posts.delete_one({"id": post_id})
    if res.deleted_count == 0:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok({"deleted": True})
