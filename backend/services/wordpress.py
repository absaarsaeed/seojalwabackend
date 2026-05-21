"""Real WordPress REST API publisher.

Auth strategy:
- `site.apiKey` is used by the WordPress plugin to authenticate when polling.
- For *outbound* posting, we use WordPress application passwords stored on the
  Site record (`wordpressToken`, encrypted with Fernet).
"""
from __future__ import annotations

import base64
import logging
from typing import Optional

import httpx

from core.encryption import decrypt
from services import storage

logger = logging.getLogger("jalwa.wordpress")


def _auth_header(username: str, app_password: str) -> str:
    raw = f"{username}:{app_password}".encode()
    return "Basic " + base64.b64encode(raw).decode()


async def _media_upload(site_url: str, headers: dict, image_url: str,
                        filename: str = "hero.jpg") -> Optional[int]:
    """Download the image from R2 and upload to WP media library."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            img = await client.get(image_url)
            img.raise_for_status()
            up = await client.post(
                f"{site_url.rstrip('/')}/wp-json/wp/v2/media",
                headers={
                    **headers,
                    "Content-Type": img.headers.get("Content-Type", "image/jpeg"),
                    "Content-Disposition": f'attachment; filename="{filename}"',
                },
                content=img.content,
            )
            up.raise_for_status()
            return up.json().get("id")
    except Exception as e:
        logger.warning("WP media upload failed: %s", e)
        return None


async def publish_article(site: dict, article: dict) -> dict:
    """Publish to WordPress via REST API. Returns
    `{success, cmsPostId?, cmsUrl?, error?}`.

    Adds:
      • white-label "Powered by SEO Jalwa" footer when plan.whiteLabel=False
      • WordPress `categories` from article.wordpressCategoryId
    """
    site_url = site.get("url", "").rstrip("/")
    if not site_url.startswith("http"):
        site_url = f"https://{site_url}"

    username = site.get("wordpressUsername") or "admin"
    app_password = decrypt(site.get("wordpressToken")) if site.get(
        "wordpressToken") else None

    if not app_password:
        # No credentials yet — the plugin will pull articles via /api/plugin
        # pending endpoint instead. We mark the article as SCHEDULED.
        logger.info("WP token missing for site=%s — leaving for plugin pull",
                    site.get("id"))
        return {"success": False, "error": "WP token missing — handled by plugin"}

    headers = {"Authorization": _auth_header(username, app_password)}

    # Featured image
    featured_id: Optional[int] = None
    if article.get("featuredImageUrl"):
        featured_id = await _media_upload(site_url, headers,
                                          article["featuredImageUrl"])

    # White-label branding (Part 10): append footer when plan does NOT
    # have white_label enabled.
    content = article.get("content", "")
    white_label = False
    try:
        from core.database import get_db
        sub = await get_db().subscriptions.find_one(
            {"userId": site.get("userId"),
             "status": {"$in": ["ACTIVE", "TRIALING"]}}, {"_id": 0})
        if sub and sub.get("planId"):
            plan = await get_db().plans.find_one(
                {"id": sub["planId"]}, {"_id": 0})
            white_label = bool((plan or {}).get("whiteLabel"))
    except Exception:
        white_label = False
    if not white_label and "seojalwa.com" not in content:
        content += ("\n\n<p><small>Published with "
                    "<a href=\"https://seojalwa.com\">SEO Jalwa</a>"
                    "</small></p>")

    payload: dict = {
        "title": article.get("title", ""),
        "content": content,
        "status": "publish",
        "excerpt": article.get("excerpt", "") or "",
        "slug": article.get("slug", ""),
        "meta": {
            "_yoast_wpseo_title": article.get("metaTitle", ""),
            "_yoast_wpseo_metadesc": article.get("metaDescription", ""),
        },
    }
    if featured_id:
        payload["featured_media"] = featured_id
    # Part 4 — intelligent category selection.
    cat_id = article.get("wordpressCategoryId")
    if cat_id:
        try:
            payload["categories"] = [int(cat_id)]
        except (TypeError, ValueError):
            pass

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{site_url}/wp-json/wp/v2/posts",
                headers={**headers, "Content-Type": "application/json"},
                json=payload,
            )
            r.raise_for_status()
            data = r.json()
            return {
                "success": True,
                "cmsPostId": str(data.get("id")),
                "cmsUrl": data.get("link"),
            }
    except Exception as e:
        logger.exception("WP publish failed: %s", e)
        return {"success": False, "error": str(e)}
