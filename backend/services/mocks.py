"""Mock 3rd-party service interfaces with TODO markers.

Each function has a clean signature and a clear TODO showing what real API
calls to wire up when credentials are provided.
"""
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Optional

logger = logging.getLogger("jalwa.mocks")


# ============================================================================
# RESEND — Email
# ----------------------------------------------------------------------------
# TODO: pip install resend
# TODO: import resend; resend.api_key = await get_api_key("resend")
# TODO: resend.Emails.send({
#           "from": os.environ["RESEND_FROM_EMAIL"],
#           "to": [to], "subject": subject, "html": html,
#       })
# ============================================================================
async def send_email(to: str, subject: str, html: str, template: str = "generic") -> dict:
    logger.info("[MOCK RESEND] template=%s to=%s subject=%s", template, to, subject)
    return {"id": f"mock_email_{uuid.uuid4()}", "to": to, "status": "queued"}


# ============================================================================
# LEMONSQUEEZY — Payments / Subscriptions
# ----------------------------------------------------------------------------
# TODO: hit https://api.lemonsqueezy.com/v1/checkouts with Authorization: Bearer <LEMONSQUEEZY_API_KEY>
# TODO: subscribe to webhook at /api/billing/webhook, verify X-Signature with LEMONSQUEEZY_WEBHOOK_SECRET
# ============================================================================
async def create_checkout(user_id: str, plan_id: str, interval: str) -> dict:
    logger.info("[MOCK LemonSqueezy] checkout user=%s plan=%s", user_id, plan_id)
    return {
        "checkoutUrl": f"https://mock-lemonsqueezy.test/checkout/{uuid.uuid4()}",
        "checkoutId": f"mock_co_{uuid.uuid4()}",
    }


async def lemonsqueezy_refund(invoice_id: str) -> dict:
    logger.info("[MOCK LemonSqueezy] refund %s", invoice_id)
    return {"refunded": True, "refundId": f"mock_rf_{uuid.uuid4()}"}


async def lemonsqueezy_create_discount(code: str, value: float, type_: str) -> dict:
    logger.info("[MOCK LemonSqueezy] discount %s %s %s", code, value, type_)
    return {"id": f"mock_disc_{uuid.uuid4()}", "code": code}


def verify_lemonsqueezy_signature(payload: bytes, signature: str) -> bool:
    # TODO: HMAC SHA256 with LEMONSQUEEZY_WEBHOOK_SECRET — compare to signature header
    return True


# ============================================================================
# CLOUDFLARE R2 — Object storage (S3-compatible)
# ----------------------------------------------------------------------------
# TODO: pip install boto3
# TODO: boto3.client("s3", endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
#                    aws_access_key_id=..., aws_secret_access_key=...)
# TODO: client.put_object(Bucket=R2_BUCKET_NAME, Key=key, Body=bytes, ContentType=...)
# ============================================================================
async def upload_to_r2(key: str, content: bytes, content_type: str = "image/png") -> str:
    logger.info("[MOCK R2] upload key=%s bytes=%d", key, len(content))
    return f"https://mock-r2.test/{key}"


# ============================================================================
# DATAFORSEO — Keyword research / SERP
# ----------------------------------------------------------------------------
# TODO: HTTP basic auth with DATAFORSEO_LOGIN / DATAFORSEO_PASSWORD
# TODO: POST https://api.dataforseo.com/v3/keywords_data/google_ads/search_volume/live
# ============================================================================
async def keyword_research(terms: list[str]) -> list[dict]:
    logger.info("[MOCK DataForSEO] research %d terms", len(terms))
    out = []
    for t in terms:
        out.append({
            "term": t,
            "monthlySearchVolume": (abs(hash(t)) % 12000) + 100,
            "difficulty": (abs(hash(t)) % 100),
        })
    return out


# ============================================================================
# GOOGLE SEARCH CONSOLE — Analytics
# ----------------------------------------------------------------------------
# TODO: pip install google-auth google-auth-oauthlib google-api-python-client
# TODO: build("searchconsole","v1",credentials=Credentials(token=...))
#       .searchanalytics().query(siteUrl=..., body={...}).execute()
# ============================================================================
async def gsc_fetch_performance(site_url: str, days: int = 7) -> dict:
    logger.info("[MOCK GSC] fetch %s last %d days", site_url, days)
    return {
        "totalClicks": 1234,
        "totalImpressions": 45678,
        "avgCTR": 2.7,
        "avgPosition": 14.3,
        "rows": [],
    }


async def gsc_exchange_code(code: str) -> dict:
    logger.info("[MOCK GSC] OAuth exchange code")
    return {"access_token": "mock_gsc_token", "refresh_token": "mock_refresh"}


# ============================================================================
# CMS PUBLISH ADAPTERS
# ----------------------------------------------------------------------------
# WordPress — plugin pulls via /api/plugin/articles/pending. No outbound call.
# Webflow — POST /collections/{id}/items with Bearer token
# Ghost — POST {site}/ghost/api/admin/posts/  (JWT signed with Admin API key)
# HubSpot — POST /cms/v3/blogs/posts with Bearer token
# Wix — POST /blog/v3/draft-posts with apiKey
# Notion — POST /v1/pages with Authorization: Bearer
# ============================================================================
async def publish_to_cms(platform: str, site: dict, article: dict) -> dict:
    logger.info("[MOCK CMS] publish %s article=%s to %s", platform,
                article.get("id"), site.get("url"))
    post_id = f"mock_{platform}_{uuid.uuid4()}"
    return {
        "success": True,
        "cmsPostId": post_id,
        "cmsUrl": f"https://{site.get('url', 'example.com')}/{article.get('slug', '')}",
    }


# ============================================================================
# SOCIAL PUBLISH ADAPTERS
# ----------------------------------------------------------------------------
# Instagram/Facebook — POST /me/media + /me/media_publish via Meta Graph API
# LinkedIn — POST /v2/ugcPosts
# Twitter — POST /2/tweets with OAuth2 user context
# Pinterest — POST /v5/pins
# YouTube — for Shorts: youtube.videos.insert (resumable upload)
# ============================================================================
async def publish_social_post(platform: str, account: dict, post: dict) -> dict:
    logger.info("[MOCK SOCIAL] publish %s to %s", post.get("id"), platform)
    return {
        "success": True,
        "platformPostId": f"mock_{platform}_{uuid.uuid4()}",
        "url": f"https://{platform}.test/p/{uuid.uuid4()}",
    }


async def get_social_oauth_url(platform: str, redirect_uri: str, state: str) -> str:
    # TODO: build platform-specific OAuth authorize URL with client_id, scopes, etc.
    return (f"https://mock-{platform}.test/oauth/authorize"
            f"?redirect_uri={redirect_uri}&state={state}")


async def social_exchange_code(platform: str, code: str) -> dict:
    # TODO: exchange auth code for access_token + refresh_token per platform.
    return {
        "access_token": f"mock_{platform}_access",
        "refresh_token": f"mock_{platform}_refresh",
        "expires_in": 3600,
        "account_name": f"My {platform.title()} Account",
        "account_id": str(uuid.uuid4()),
        "follower_count": 5000,
    }


# ============================================================================
# AI VISIBILITY — 5 models (ChatGPT, Perplexity, Gemini, Claude, Copilot)
# ----------------------------------------------------------------------------
# TODO: ChatGPT — call via emergentintegrations LlmChat (already wired)
# TODO: Perplexity — POST https://api.perplexity.ai/chat/completions
# TODO: Gemini — google-generativeai
# TODO: Claude — anthropic Python SDK
# TODO: Copilot — no public API; use Bing Search API as proxy
# ============================================================================
async def query_ai_models(brand: str, queries: list[str]) -> dict:
    """Returns per-model score 0-100 and sentiment."""
    out: dict[str, dict] = {}
    for model in ["chatgpt", "perplexity", "gemini", "claude", "copilot"]:
        score = 50 + (abs(hash(model + brand)) % 50)
        sentiment = ["POSITIVE", "NEUTRAL", "NEGATIVE", "NOT_MENTIONED"][
            abs(hash(model + brand)) % 4
        ]
        out[model] = {"score": score, "sentiment": sentiment}
    return out


# ============================================================================
# GOOGLE OAUTH — login
# ----------------------------------------------------------------------------
# TODO: pip install google-auth
# TODO: id_token.verify_oauth2_token(google_token, requests.Request(), GOOGLE_CLIENT_ID)
# ============================================================================
async def verify_google_token(token: str) -> Optional[dict]:
    if not token:
        return None
    return {
        "email": f"mock_{abs(hash(token)) % 10000}@google.test",
        "name": "Mock Google User",
        "googleId": f"mock_g_{abs(hash(token))}",
        "picture": None,
    }
