"""Real Google Search Console integration (OAuth 2.0 user flow)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger("jalwa.gsc")

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _config_ok() -> bool:
    """Sync fast-path check using env. Real check is async via _gsc_config()."""
    return bool(os.environ.get("GOOGLE_CLIENT_ID")
                and os.environ.get("GOOGLE_CLIENT_SECRET"))


async def _gsc_config() -> dict:
    from services.config import config_service
    fields = await config_service.get_fields("google_oauth")
    return {
        "client_id": (fields.get("client_id")
                       or os.environ.get("GOOGLE_CLIENT_ID", "")),
        "client_secret": (fields.get("client_secret")
                           or os.environ.get("GOOGLE_CLIENT_SECRET", "")),
        "redirect_uri": os.environ.get("GOOGLE_REDIRECT_URI", ""),
    }


# ----------------------------------------------------------------- OAuth URL
def build_authorize_url(state: str) -> Optional[str]:
    if not _config_ok():
        return None
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(
            {"web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.environ.get("GOOGLE_REDIRECT_URI", "")],
            }},
            scopes=SCOPES,
            redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI"),
        )
        url, _ = flow.authorization_url(
            access_type="offline", prompt="consent",
            include_granted_scopes="true", state=state)
        return url
    except Exception as e:
        logger.exception("GSC authorize URL build failed: %s", e)
        return None


# -------------------------------------------------------------- code → tokens
def exchange_code(code: str) -> Optional[dict]:
    if not _config_ok():
        return None
    try:
        from google_auth_oauthlib.flow import Flow
        flow = Flow.from_client_config(
            {"web": {
                "client_id": os.environ["GOOGLE_CLIENT_ID"],
                "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [os.environ.get("GOOGLE_REDIRECT_URI", "")],
            }},
            scopes=SCOPES,
            redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI"),
        )
        flow.fetch_token(code=code)
        creds = flow.credentials
        return {
            "access_token": creds.token,
            "refresh_token": creds.refresh_token,
            "expiry": creds.expiry.isoformat() if creds.expiry else None,
        }
    except Exception as e:
        logger.exception("GSC code exchange failed: %s", e)
        return None


# ---------------------------------------------------------------- credentials
def _build_credentials(access_token: str, refresh_token: Optional[str]):
    from google.oauth2.credentials import Credentials
    return Credentials(
        token=access_token,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        scopes=SCOPES,
    )


# ---------------------------------------------------------- fetch performance
async def fetch_performance(site_url: str, access_token: str,
                             refresh_token: Optional[str], days: int = 30
                             ) -> dict:
    """Returns rows of {keys: [query, page], clicks, impressions, ctr, position}."""
    cfg = await _gsc_config()
    if not (cfg["client_id"] and cfg["client_secret"]) or not access_token:
        return {"rows": [], "configured": False}
    try:
        from googleapiclient.discovery import build
        creds = _build_credentials(access_token, refresh_token,
                                    cfg["client_id"], cfg["client_secret"])
        service = build("searchconsole", "v1", credentials=creds,
                        cache_discovery=False)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        site = site_url if site_url.startswith("http") else f"https://{site_url}"
        request_body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": 1000,
        }
        resp = service.searchanalytics().query(siteUrl=site,
                                               body=request_body).execute()
        return {"rows": resp.get("rows", []), "configured": True}
    except Exception as e:
        logger.warning("GSC fetch failed for %s: %s", site_url, e)
        return {"rows": [], "configured": False, "error": str(e)}
    """Returns rows of {keys: [query, page], clicks, impressions, ctr, position}."""
    if not _config_ok() or not access_token:
        return {"rows": [], "configured": False}
    try:
        from googleapiclient.discovery import build
        creds = _build_credentials(access_token, refresh_token)
        service = build("searchconsole", "v1", credentials=creds,
                        cache_discovery=False)
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=days)
        # GSC requires the URL exactly as the user verified it; we try as-is
        site = site_url if site_url.startswith("http") else f"https://{site_url}"
        request_body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": 1000,
        }
        resp = service.searchanalytics().query(siteUrl=site,
                                               body=request_body).execute()
        return {"rows": resp.get("rows", []), "configured": True}
    except Exception as e:
        logger.warning("GSC fetch failed for %s: %s", site_url, e)
        return {"rows": [], "configured": False, "error": str(e)}


async def test_gsc() -> dict:
    if not _config_ok():
        return {"success": False, "message": "GOOGLE_CLIENT_* not configured"}
    return {"success": True, "message": "GSC OAuth client configured"}
