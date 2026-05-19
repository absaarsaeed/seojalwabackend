"""Admin API-key management — admin-panel UI backend.

Every key is stored encrypted in `api_configs`. Cached in memory for 5 min via
`services.config.ConfigService`. Catalogue metadata in `services/api_catalog.py`.
"""
import time
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok
from core.security import utcnow_iso
from services.api_catalog import CATALOG, CATALOG_BY_KEY, all_keys
from services.config import build_admin_view, config_service

router = APIRouter(prefix="/admin/api-keys", tags=["admin-api-keys"],
                   dependencies=[Depends(get_admin_session)])


class SaveBody(BaseModel):
    """Accepts BOTH request shapes for backward compatibility:

    SHAPE 1 (legacy): {"value": "sk-proj-..."}
        - Mapped to the first required catalogue field (typically `api_key`).
    SHAPE 2 (current): {"fields": {"api_key": "sk-proj-...", ...}}
        - Persisted as-is (dict of fields).
    """
    fields: dict[str, Any] | None = None
    value: str | None = None

    class Config:
        extra = "allow"


def _resolve_fields(key: str, body: SaveBody) -> dict[str, Any]:
    """Normalise either request shape into a {field_name: value} dict."""
    # SHAPE 2: explicit fields dict wins
    if body.fields is not None:
        return body.fields or {}

    # SHAPE 1: single value → map to primary field of the catalogue entry
    if body.value is not None:
        entry = CATALOG_BY_KEY.get(key.lower())
        if not entry or not entry.get("fields"):
            return {"api_key": body.value}
        # Prefer the first required field; fall back to the first field.
        primary = next(
            (f["name"] for f in entry["fields"] if f.get("required")),
            entry["fields"][0]["name"],
        )
        return {primary: body.value}

    return {}


# ============================================================ LIST
@router.get("")
async def list_keys():
    db = get_db()
    out = []
    for entry in CATALOG:
        rec = await db.api_configs.find_one({"key": entry["key"]}, {"_id": 0})
        fields_values = await config_service.get_fields(entry["key"])
        out.append(build_admin_view(entry["key"], rec, fields_values))
    return ok(out, message=f"{len(out)} services available")


# ============================================================ SUPPORTED LIST
@router.get("/supported")
async def supported():
    return ok(all_keys())


# ============================================================ SINGLE
@router.get("/{key}")
async def get_one(key: str):
    if key.lower() not in CATALOG_BY_KEY:
        raise APIError("Unknown service", "NOT_FOUND", 404)
    db = get_db()
    rec = await db.api_configs.find_one({"key": key.lower()}, {"_id": 0})
    fields_values = await config_service.get_fields(key.lower())
    return ok(build_admin_view(key.lower(), rec, fields_values))


# ============================================================ SAVE
@router.put("/{key}")
async def save(key: str, body: SaveBody):
    if key.lower() not in CATALOG_BY_KEY:
        raise APIError("Unknown service", "NOT_FOUND", 404)
    fields_to_save = _resolve_fields(key.lower(), body)
    if not fields_to_save:
        raise APIError("Request must include either 'fields' or 'value'",
                       "VALIDATION_ERROR", 422)
    rec = await config_service.set_fields(key.lower(), fields_to_save)
    # Compose response with masked values for confirmation
    fresh = await config_service.get_fields(key.lower())
    view = build_admin_view(key.lower(), rec,  fresh)
    return ok({"saved": True,
               "key": key.lower(),
               "masked_values": {f["name"]: f["value"] for f in view["fields"]},
               "status": view["status"]},
              "Key saved (effective immediately, cache invalidated)")


# ============================================================ TEST
@router.post("/{key}/test")
async def test(key: str):
    if key.lower() not in CATALOG_BY_KEY:
        raise APIError("Unknown service", "NOT_FOUND", 404)
    fields = await config_service.get_fields(key.lower())
    t0 = time.perf_counter()
    result = await _run_test(key.lower(), fields)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    db = get_db()
    await db.api_configs.update_one(
        {"key": key.lower()},
        {"$set": {"lastTestedAt": utcnow_iso(),
                  "testStatus": "SUCCESS" if result["success"] else "FAILED"}},
        upsert=True)
    return ok({**result, "latency_ms": latency_ms,
               "tested_at": utcnow_iso()})


# --------------------------------------------------------------- per-service
async def _run_test(key: str, fields: dict) -> dict:
    """Service-specific real connection tests."""
    if not any(fields.values()):
        return {"success": False, "message": "No credentials configured"}

    if key == "openai":
        try:
            from openai import AsyncOpenAI
            client = AsyncOpenAI(api_key=fields.get("api_key", ""))
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": "Say OK"}],
                max_tokens=5)
            content = (resp.choices[0].message.content or "").strip()
            return {"success": bool(content),
                    "message": content or "Empty response"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "anthropic":
        try:
            from anthropic import AsyncAnthropic
            client = AsyncAnthropic(api_key=fields.get("api_key", ""))
            msg = await client.messages.create(
                model="claude-3-haiku-20240307",
                max_tokens=10,
                messages=[{"role": "user", "content": "Say OK"}])
            txt = "".join(b.text for b in msg.content
                          if getattr(b, "type", "text") == "text")
            return {"success": bool(txt), "message": txt[:200]
                    or "Empty response"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "gemini":
        try:
            import google.generativeai as genai
            genai.configure(api_key=fields.get("api_key", ""))
            model = genai.GenerativeModel("gemini-1.5-flash")
            resp = await model.generate_content_async("Say OK")
            return {"success": bool(resp.text),
                    "message": (resp.text or "Empty")[:200]}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "perplexity":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=20) as client:
                r = await client.post(
                    "https://api.perplexity.ai/chat/completions",
                    headers={"Authorization": f"Bearer {fields.get('api_key','')}",
                             "Content-Type": "application/json"},
                    json={"model": "llama-3.1-sonar-small-128k-online",
                          "messages": [{"role": "user", "content": "Say OK"}]})
                r.raise_for_status()
                msg = r.json()["choices"][0]["message"]["content"]
                return {"success": True, "message": (msg or "OK")[:200]}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "sendgrid":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    "https://api.sendgrid.com/v3/user/account",
                    headers={"Authorization": f"Bearer {fields.get('api_key','')}"})
                if r.status_code == 200:
                    return {"success": True,
                            "message": f"Authenticated ({r.json().get('type','ok')})"}
                return {"success": False,
                        "message": f"HTTP {r.status_code}: {r.text[:160]}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "cloudflare_r2":
        try:
            import boto3
            from botocore.config import Config
            client = boto3.client(
                "s3",
                endpoint_url=f"https://{fields.get('account_id','')}.r2.cloudflarestorage.com",
                aws_access_key_id=fields.get("access_key_id", ""),
                aws_secret_access_key=fields.get("secret_access_key", ""),
                config=Config(signature_version="s3v4"),
                region_name="auto",
            )
            client.list_objects_v2(Bucket=fields.get("bucket_name",
                                                     "seojalwa-assets"),
                                   MaxKeys=1)
            return {"success": True,
                    "message": f"Bucket '{fields.get('bucket_name')}' reachable"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "dataforseo":
        try:
            import base64
            import httpx
            login = fields.get("login", "")
            pw = fields.get("password", "")
            token = base64.b64encode(f"{login}:{pw}".encode()).decode()
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    "https://api.dataforseo.com/v3/appendix/user_data",
                    headers={"Authorization": f"Basic {token}"})
                if r.status_code == 200:
                    return {"success": True,
                            "message": "Account info fetched"}
                return {"success": False,
                        "message": f"HTTP {r.status_code}: {r.text[:160]}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    if key == "lemonsqueezy":
        try:
            import httpx
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    "https://api.lemonsqueezy.com/v1/stores",
                    headers={"Authorization": f"Bearer {fields.get('api_key','')}",
                             "Accept": "application/vnd.api+json"})
                if r.status_code == 200:
                    return {"success": True,
                            "message": "Stores fetched"}
                return {"success": False,
                        "message": f"HTTP {r.status_code}: {r.text[:160]}"}
        except Exception as e:
            return {"success": False, "message": str(e)[:200]}

    # OAuth-only services — cannot be tested without user interaction
    if key in {"google_oauth", "meta", "linkedin", "twitter", "pinterest"}:
        # Sanity-check: both fields present
        required = [f["name"] for f in CATALOG_BY_KEY[key]["fields"]
                    if f.get("required")]
        missing = [n for n in required if not fields.get(n)]
        if missing:
            return {"success": False,
                    "message": f"Missing fields: {', '.join(missing)}"}
        return {"success": True,
                "message": ("OAuth app credentials present. They will be "
                            "validated when a user connects their account.")}

    return {"success": False, "message": "Unknown service"}
