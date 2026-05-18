"""Public endpoints (no auth required)."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr

from core.database import get_db
from core.rate_limit import rate_limit
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso
from services import mocks

router = APIRouter(tags=["public"])


class ContactReq(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str


class DemoReq(BaseModel):
    url: str


@router.get("/plans")
async def public_plans():
    rows = await get_db().plans.find(
        {"isActive": True}, {"_id": 0}).sort("sortOrder", 1).to_list(50)
    return ok(rows)


@router.get("/blog")
async def public_blog(page: int = 1, limit: int = 10,
                      status: str = "published"):
    db = get_db()
    q = {"status": status.upper()}
    total = await db.blog_posts.count_documents(q)
    rows = await db.blog_posts.find(q, {"_id": 0}).sort(
        "publishedAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/blog/{slug}")
async def public_blog_post(slug: str):
    post = await get_db().blog_posts.find_one(
        {"slug": slug, "status": "PUBLISHED"}, {"_id": 0})
    if not post:
        raise APIError("Post not found", "NOT_FOUND", 404)
    return ok(post)


@router.post("/contact")
async def contact(body: ContactReq):
    doc = {
        "id": str(uuid.uuid4()), "name": body.name, "email": body.email,
        "subject": body.subject, "message": body.message,
        "createdAt": utcnow_iso(),
    }
    await get_db().contacts.insert_one(dict(doc))
    await mocks.send_email(
        to="hello@seojalwa.com", template="contact",
        subject=f"New contact: {body.subject}",
        html=f"<p>From {body.name} ({body.email})</p><p>{body.message}</p>")
    return created({"received": True}, "Thanks, we'll be in touch")


@router.post("/ai-visibility/demo",
             dependencies=[Depends(rate_limit("ai-demo", 5, 3600))])
async def ai_demo(body: DemoReq):
    """Personalised dummy result based on submitted URL."""
    url = body.url
    score_seed = abs(hash(url)) % 100
    overall = 40 + (score_seed % 50)
    return ok({
        "url": url,
        "overallScore": overall,
        "models": {
            "chatgpt": {"score": (overall + 5) % 100, "sentiment": "NEUTRAL"},
            "perplexity": {"score": (overall - 8) % 100, "sentiment": "POSITIVE"},
            "gemini": {"score": (overall + 2) % 100, "sentiment": "NOT_MENTIONED"},
            "claude": {"score": (overall - 3) % 100, "sentiment": "NEUTRAL"},
            "copilot": {"score": (overall + 7) % 100, "sentiment": "POSITIVE"},
        },
        "recommendations": [
            f"Optimise content on {url} for direct-answer queries",
            "Improve E-E-A-T signals across the site",
            "Add structured data and FAQ schema to pillar pages",
        ],
    })
