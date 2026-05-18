"""In-memory cache for API config loaded from DB with env fallback.

The admin can add encrypted API keys via /api/admin/api-keys. This loader
exposes get_api_key(service) which returns DB value first, then environment.
Cache refreshes every 5 minutes.
"""
import asyncio
import logging
import os
import time
from typing import Optional

from core.database import get_db
from core.encryption import decrypt

logger = logging.getLogger("jalwa.api_keys")

_cache: dict[str, str] = {}
_last_refresh: float = 0.0
_TTL_SECONDS = 300


SUPPORTED_KEYS = [
    "openai", "anthropic", "gemini", "perplexity", "resend", "dataforseo",
    "r2_account_id", "r2_access_key_id", "r2_secret_access_key",
    "lemonsqueezy_api_key", "lemonsqueezy_store_id",
    "meta_app_id", "meta_app_secret",
    "linkedin_client_id", "linkedin_client_secret",
    "twitter_client_id", "twitter_client_secret",
    "pinterest_app_id", "pinterest_app_secret",
    "google_client_id", "google_client_secret",
]


async def refresh_cache():
    global _cache, _last_refresh
    db = get_db()
    docs = await db.api_configs.find({"isActive": True}, {"_id": 0}).to_list(500)
    new_cache: dict[str, str] = {}
    for d in docs:
        plain = decrypt(d.get("encryptedValue"))
        if plain:
            new_cache[d["key"].lower()] = plain
    _cache = new_cache
    _last_refresh = time.time()
    logger.info("API key cache refreshed: %d keys", len(_cache))


async def get_api_key(service: str) -> Optional[str]:
    if time.time() - _last_refresh > _TTL_SECONDS:
        try:
            await refresh_cache()
        except Exception as e:
            logger.warning("Failed refresh: %s", e)
    val = _cache.get(service.lower())
    if val:
        return val
    return os.environ.get(service.upper())


def schedule_cache_refresh():
    async def _loop():
        while True:
            try:
                await refresh_cache()
            except Exception as e:
                logger.warning("cache refresh err: %s", e)
            await asyncio.sleep(_TTL_SECONDS)
    asyncio.create_task(_loop())
