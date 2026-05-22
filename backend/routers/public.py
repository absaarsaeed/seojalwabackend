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


def _feature_display(key: str, value, plan_is_free: bool) -> dict:
    """Format a single feature for the plan-selection page."""
    labels = {
        "articlesPerMonth": "Articles per month",
        "websiteConnections": "Website connections",
        "aiScansPerMonth": "AI Visibility scans per month",
        "socialPostsPerMonth": "Social posts per month",
        "teamSeats": "Team seats",
        "whiteLabel": "White-label (no SEO Jalwa branding)",
        "gscConnection": "Google Search Console integration",
        "prioritySupport": "Priority support",
    }
    label = labels.get(key, key)
    if isinstance(value, bool):
        display = label
    elif key == "articlesPerMonth":
        display = f"{value} articles/month"
    elif key == "websiteConnections":
        display = f"{value} {'website' if value == 1 else 'websites'}"
    elif key == "aiScansPerMonth":
        display = f"{value} AI scans/month"
    elif key == "socialPostsPerMonth":
        display = f"{value} social posts/month"
    elif key == "teamSeats":
        display = f"{value} team seat{'s' if value != 1 else ''}"
    else:
        display = f"{value} {label}"
    return {"key": key, "label": label, "value": value,
             "displayValue": display}


@router.get("/plans/selection")
async def plans_selection():
    """Plans formatted for the public plan-selection / pricing page.

    Only enabled features per plan are returned, each with a
    human-readable `displayValue`. Highlights the 'Growth' plan.
    """
    rows = await get_db().plans.find(
        {"isActive": True}, {"_id": 0}).sort(
        [("order", 1), ("sortOrder", 1)]).to_list(50)

    plans_out = []
    for p in rows:
        feats = p.get("features") or {}
        enabled = []
        for k, meta in feats.items():
            if isinstance(meta, dict) and meta.get("enabled"):
                v = meta.get("value")
                # Skip boolean-feature rows with value=False
                if isinstance(v, bool) and not v:
                    continue
                enabled.append({
                    **_feature_display(k, v, bool(p.get("isFree"))),
                    "enabled": True,
                })
        plans_out.append({
            "id": p["id"],
            "slug": p.get("slug") or p.get("name", "").lower(),
            "name": p.get("name"),
            "description": p.get("description", ""),
            "monthlyPrice": p.get("monthlyPrice", 0),
            "annualPrice": p.get("annualPrice", 0),
            "isFree": bool(p.get("isFree")),
            "features": enabled,
            "cta": ("Get started free" if p.get("isFree")
                    else f"Upgrade to {p.get('name')}"),
            "highlighted": p.get("name") == "Growth",
        })
    return ok({"plans": plans_out, "trialDays": 0})


@router.get("/plans")
async def public_plans():
    rows = await get_db().plans.find(
        {"isActive": True}, {"_id": 0}).sort("sortOrder", 1).to_list(50)
    # Backward compat — ensure both `cmsConnections` and `websiteConnections`
    # are present on every plan (Master prompt Part 11).
    for r in rows:
        if "websiteConnections" not in r and "cmsConnections" in r:
            r["websiteConnections"] = r["cmsConnections"]
        if "cmsConnections" not in r and "websiteConnections" in r:
            r["cmsConnections"] = r["websiteConnections"]
    return ok(rows)


@router.get("/settings/public")
async def public_settings():
    """Subset of admin settings safe to expose anonymously."""
    db = get_db()
    keys = ["trial_days", "plugin_version", "plugin_download_url"]
    out: dict = {}
    for k in keys:
        doc = await db.settings.find_one({"key": k}, {"_id": 0})
        out[k] = (doc or {}).get("value")
    # Fallback defaults
    if not out.get("trial_days"):
        out["trial_days"] = 14
    return ok(out)


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
