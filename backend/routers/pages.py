"""Public marketing pages — integrations catalogue, etc."""
from fastapi import APIRouter

from core.response import ok

router = APIRouter(prefix="/pages", tags=["public-pages"])


# Single source of truth for the integrations page. The flag `isAvailable`
# controls whether the card is normal or greyed-out "Coming Soon" on /integrations.
_INTEGRATIONS = [
    # ─── CMS / Website platforms ─────────────────────────────────────
    {"category": "Website Platforms", "key": "wordpress",
     "name": "WordPress",
     "description": "Self-hosted WordPress sites via our official plugin.",
     "logo": "wordpress", "isAvailable": True},
    {"category": "Website Platforms", "key": "shopify",
     "name": "Shopify", "logo": "shopify", "isAvailable": False,
     "description": "Storefront blog publishing."},
    {"category": "Website Platforms", "key": "webflow",
     "name": "Webflow", "logo": "webflow", "isAvailable": False,
     "description": "Push CMS items to your Webflow collection."},
    {"category": "Website Platforms", "key": "ghost",
     "name": "Ghost", "logo": "ghost", "isAvailable": False,
     "description": "Native Ghost integration."},
    {"category": "Website Platforms", "key": "wix",
     "name": "Wix", "logo": "wix", "isAvailable": False,
     "description": "Publish to your Wix blog."},
    {"category": "Website Platforms", "key": "squarespace",
     "name": "Squarespace", "logo": "squarespace", "isAvailable": False,
     "description": "Squarespace blog automation."},
    {"category": "Website Platforms", "key": "nextjs",
     "name": "Next.js / Headless", "logo": "nextjs", "isAvailable": False,
     "description": "Generic webhook for headless stacks."},
    {"category": "Website Platforms", "key": "notion",
     "name": "Notion", "logo": "notion", "isAvailable": False,
     "description": "Publish drafts into a Notion database."},
    {"category": "Website Platforms", "key": "hubspot",
     "name": "HubSpot", "logo": "hubspot", "isAvailable": False,
     "description": "HubSpot CMS blog integration."},
    # ─── Social ──────────────────────────────────────────────────────
    {"category": "Social Autopilot", "key": "instagram",
     "name": "Instagram", "logo": "instagram", "isAvailable": False,
     "description": "Auto-resize hero images + caption scheduling."},
    {"category": "Social Autopilot", "key": "facebook",
     "name": "Facebook Pages", "logo": "facebook", "isAvailable": False,
     "description": "Schedule articles to your Page."},
    {"category": "Social Autopilot", "key": "linkedin",
     "name": "LinkedIn", "logo": "linkedin", "isAvailable": False,
     "description": "Personal + company-page posts."},
    {"category": "Social Autopilot", "key": "twitter",
     "name": "X (Twitter)", "logo": "twitter", "isAvailable": False,
     "description": "Threads from your articles."},
    {"category": "Social Autopilot", "key": "pinterest",
     "name": "Pinterest", "logo": "pinterest", "isAvailable": False,
     "description": "Pin images + descriptions to boards."},
    {"category": "Social Autopilot", "key": "youtube",
     "name": "YouTube Shorts", "logo": "youtube", "isAvailable": False,
     "description": "Auto-generate Shorts from your articles."},
]


@router.get("/integrations")
async def integrations():
    by_cat: dict[str, list[dict]] = {}
    for item in _INTEGRATIONS:
        by_cat.setdefault(item["category"], []).append(item)
    return ok({
        "categories": [
            {"name": cat, "items": items} for cat, items in by_cat.items()
        ],
        "platforms": _INTEGRATIONS,
    })
