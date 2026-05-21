"""Auto site analyser + auto-setup pipeline.

Runs after WordPress is verified (`/api/plugin/verify`) or after the user
clicks "Verify connection" (`/api/sites/{id}/verify-connection`). Fetches
the homepage + recent posts + categories, asks GPT-4o for niche, audience,
content style, and recommended settings, then writes:

* `article_settings` (auto-configured)
* up to 10 AI-suggested `search_terms`
* `categoryMapping` on the site
* `analyzed=true` + `analyzedAt` on the site
* SITE_ANALYZED notification + USER_ACTIVITY entry

It then chains into `setup_trial_articles` (Part 2).
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from core.database import get_db
from core.security import utcnow_iso
from services.llm import chat_completion
from services.notifications import create_notification

logger = logging.getLogger("jalwa.site_analyzer")


# ─────────────────────────────────────────────────────────── WP REST helpers
async def fetch_url(url: str, timeout: int = 15) -> str:
    try:
        async with httpx.AsyncClient(timeout=timeout,
                                      follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": "SEO Jalwa Analyzer"})
            r.raise_for_status()
            return r.text
    except Exception as e:
        logger.warning("fetch_url(%s) failed: %s", url, e)
        return ""


async def fetch_wordpress_posts(site_url: str, limit: int = 10) -> list[dict]:
    base = site_url.rstrip("/")
    api = f"{base}/wp-json/wp/v2/posts?per_page={limit}&_fields=id,title,excerpt,link,categories"
    try:
        async with httpx.AsyncClient(timeout=15,
                                      follow_redirects=True) as c:
            r = await c.get(api, headers={"User-Agent": "SEO Jalwa Analyzer"})
            if r.status_code == 200:
                raw = r.json()
                return [{
                    "title": (p.get("title") or {}).get("rendered", ""),
                    "excerpt": _strip_html((p.get("excerpt") or {}).get(
                        "rendered", ""))[:300],
                    "url": p.get("link", ""),
                    "categoryIds": p.get("categories", []),
                } for p in raw[:limit]]
    except Exception as e:
        logger.info("WP posts unavailable for %s: %s", site_url, e)
    return []


async def fetch_wordpress_categories(site_url: str) -> list[dict]:
    base = site_url.rstrip("/")
    api = f"{base}/wp-json/wp/v2/categories?per_page=50&_fields=id,name,slug"
    try:
        async with httpx.AsyncClient(timeout=15,
                                      follow_redirects=True) as c:
            r = await c.get(api, headers={"User-Agent": "SEO Jalwa Analyzer"})
            if r.status_code == 200:
                return [{"id": c.get("id"), "name": c.get("name"),
                          "slug": c.get("slug")} for c in r.json()]
    except Exception as e:
        logger.info("WP categories unavailable for %s: %s", site_url, e)
    return []


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s or "").strip()


# ─────────────────────────────────────────────────────────── analyser
SYSTEM_PROMPT = (
    "You are an expert SEO consultant. Analyse the website provided in "
    "the user message and return ONLY a valid JSON object (no preamble, "
    "no markdown fences) with exactly these keys: niche (string), "
    "targetAudience (string), contentStyle (object with tone "
    "[professional|casual|technical], avgWordCount [int], commonTopics "
    "[string[]], writingLanguage [string]), recommendedSettings (object "
    "with articleLength [WORDS_1500|WORDS_2000|WORDS_3000], "
    "publishingFrequency [int 3-7], writingInstructions [string], "
    "includeHeroImages [bool], includeTableOfContents [bool]), "
    "recommendedTopics (array of 10 string topic ideas), categoryMapping "
    "(object mapping topic→WordPress category name from the list)."
)


async def _ask_gpt(prompt_payload: str) -> dict[str, Any]:
    raw = await chat_completion(SYSTEM_PROMPT, prompt_payload, model="gpt-4o")
    cleaned = (raw or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```", 2)[1]
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip("`\n ")
    try:
        return json.loads(cleaned)
    except Exception:
        logger.warning("analyser couldn't parse LLM output: %s", raw[:200])
        return {}


def _fallback_result(site_name: str, posts: list[dict]) -> dict:
    """Used when GPT is unavailable so trial setup still works."""
    return {
        "niche": f"Content on {site_name}",
        "targetAudience": "Readers interested in this niche",
        "contentStyle": {
            "tone": "professional", "avgWordCount": 2000,
            "commonTopics": [p["title"] for p in posts[:5]],
            "writingLanguage": "English",
        },
        "recommendedSettings": {
            "articleLength": "WORDS_2000",
            "publishingFrequency": 5,
            "writingInstructions":
                "Match the tone of existing posts. Use clear H2/H3 "
                "structure and short paragraphs.",
            "includeHeroImages": True,
            "includeTableOfContents": True,
        },
        "recommendedTopics": [
            f"{site_name} guide for beginners",
            f"How to get started with {site_name}",
            f"Top {site_name} tips",
            f"{site_name} best practices",
            f"Common mistakes in {site_name}",
            f"{site_name} tools and resources",
            f"{site_name} case studies",
            f"{site_name} trends",
            f"{site_name} FAQ",
            f"{site_name} for experts",
        ],
        "categoryMapping": {},
    }


async def analyze_and_setup_site(site_id: str) -> dict:
    db = get_db()
    site = await db.sites.find_one({"id": site_id}, {"_id": 0})
    if not site:
        logger.warning("analyze_and_setup_site: site %s not found", site_id)
        return {}

    site_url = (site.get("url") or "").rstrip("/")
    site_name = site.get("name") or site_url

    homepage_html = _strip_html(await fetch_url(site_url))[:3000]
    posts = await fetch_wordpress_posts(site_url, limit=10)
    categories = await fetch_wordpress_categories(site_url)

    prompt = (
        f"Website: {site_name} ({site_url})\n\n"
        f"Homepage text (excerpt):\n{homepage_html}\n\n"
        f"Recent posts: {json.dumps(posts[:8])}\n\n"
        f"WordPress categories: {json.dumps(categories)}")

    result = await _ask_gpt(prompt) or _fallback_result(site_name, posts)

    # Build a name→id mapping from WP categories so the publisher can use it
    cat_name_to_id = {c["name"]: c["id"] for c in categories}
    raw_mapping = result.get("categoryMapping") or {}
    # raw_mapping is topic→category-name; resolve to topic→{id, name}
    resolved_mapping: dict[str, dict] = {}
    for topic, cat_name in raw_mapping.items():
        cat_id = cat_name_to_id.get(cat_name)
        if cat_id:
            resolved_mapping[topic] = {"id": cat_id, "name": cat_name}

    # Persist article_settings (overwrites any blank/default existing row)
    settings_doc = {
        "siteId": site_id, "userId": site["userId"],
        "autoPublish": True, "delayPublishing": False,
        "includeHeroImages": result.get("recommendedSettings", {}).get(
            "includeHeroImages", True),
        "includeYoutubeVideos": False,
        "includeInfographics": True,
        "includeKeyTakeaways": True,
        "includeTableOfContents": result.get("recommendedSettings", {}).get(
            "includeTableOfContents", True),
        "addExternalLinks": True,
        "articleLength": result.get("recommendedSettings", {}).get(
            "articleLength", "WORDS_2000"),
        "publishingFrequency": int(result.get("recommendedSettings", {}).get(
            "publishingFrequency", 5)),
        "writingLanguage": result.get("contentStyle", {}).get(
            "writingLanguage", "English"),
        "writingInstructions": result.get("recommendedSettings", {}).get(
            "writingInstructions", ""),
        "websiteTitle": site_name,
        "websiteDescription": homepage_html[:200],
        "targetCountry": "Worldwide", "targetCity": "",
        "whatYouSell": "", "whatYouDontSell": "",
        "imageryPrompt": "",
        "autoConfigured": True,
        "analysisData": result,
        "updatedAt": utcnow_iso(),
    }
    await db.article_settings.update_one(
        {"siteId": site_id},
        {"$set": settings_doc,
         "$setOnInsert": {"id": str(uuid.uuid4()),
                           "createdAt": utcnow_iso()}},
        upsert=True)

    # Persist suggested search terms (skip duplicates by lowercased term)
    existing_terms = set()
    async for t in db.search_terms.find(
            {"siteId": site_id}, {"_id": 0, "term": 1}):
        existing_terms.add((t.get("term") or "").lower())
    inserted = 0
    for topic in (result.get("recommendedTopics") or [])[:10]:
        t = (topic or "").strip()
        if not t or t.lower() in existing_terms:
            continue
        await db.search_terms.insert_one({
            "id": str(uuid.uuid4()), "siteId": site_id,
            "userId": site["userId"], "term": t,
            "source": "AI_SUGGESTED", "status": "PENDING",
            "createdAt": utcnow_iso(),
        })
        existing_terms.add(t.lower())
        inserted += 1

    # Mark site as analyzed + attach mapping
    await db.sites.update_one(
        {"id": site_id},
        {"$set": {
            "categoryMapping": resolved_mapping,
            "analysis": {"niche": result.get("niche"),
                          "targetAudience": result.get("targetAudience"),
                          "tone": result.get("contentStyle", {}).get("tone")},
            "analyzed": True,
            "analyzedAt": utcnow_iso(),
            "updatedAt": utcnow_iso(),
        }})

    # Notification + activity
    try:
        await create_notification(
            site["userId"], "AI_SCAN_COMPLETE",
            f"{site_name} is ready",
            ("We analysed your site and auto-configured everything. "
             f"{inserted} keyword ideas added."),
            icon="sparkles", link="/dashboard/article-settings")
    except Exception:
        pass
    try:
        from services.activity import log_activity
        await log_activity(site["userId"], "SITE_CONNECTED",
                            metadata={"siteId": site_id,
                                       "analyzed": True})
    except Exception:
        pass

    logger.info("site analysed: %s | topics=%d categories=%d",
                site_name, inserted, len(categories))

    # Chain → trial article batch
    try:
        from services.trial import setup_trial_articles
        await setup_trial_articles(site["userId"], site_id)
    except Exception as e:
        logger.warning("trial article setup failed: %s", e)

    return result
