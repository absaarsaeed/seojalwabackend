"""Admin: API keys (encrypted in DB, masked on read)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.encryption import decrypt, encrypt, mask
from core.response import APIError, created, ok
from core.security import utcnow_iso
from services.api_keys import SUPPORTED_KEYS, refresh_cache
from services.llm import chat_completion
from services import mocks

router = APIRouter(prefix="/admin/api-keys", tags=["admin-api-keys"],
                   dependencies=[Depends(get_admin_session)])


class KeyCreate(BaseModel):
    key: str
    value: str


class KeyUpdate(BaseModel):
    value: str


@router.get("")
async def list_keys():
    rows = await get_db().api_configs.find({}, {"_id": 0}).to_list(200)
    out = []
    for r in rows:
        plain = decrypt(r.get("encryptedValue"))
        out.append({
            "key": r["key"],
            "maskedValue": mask(plain),
            "isActive": r.get("isActive", True),
            "lastTestedAt": r.get("lastTestedAt"),
            "testStatus": r.get("testStatus", "UNTESTED"),
            "updatedAt": r.get("updatedAt"),
        })
    return ok(out, message=f"{len(out)} keys configured")


@router.get("/supported")
async def supported_keys():
    return ok(SUPPORTED_KEYS)


@router.post("")
async def create_key(body: KeyCreate):
    import uuid
    db = get_db()
    existing = await db.api_configs.find_one({"key": body.key.lower()},
                                             {"_id": 0})
    enc = encrypt(body.value)
    if existing:
        await db.api_configs.update_one(
            {"key": body.key.lower()},
            {"$set": {"encryptedValue": enc, "isActive": True,
                      "updatedAt": utcnow_iso()}})
    else:
        await db.api_configs.insert_one({
            "id": str(uuid.uuid4()), "key": body.key.lower(),
            "encryptedValue": enc, "isActive": True,
            "testStatus": "UNTESTED",
            "updatedAt": utcnow_iso(),
        })
    await refresh_cache()
    return created({"key": body.key.lower()})


@router.put("/{key}")
async def update_key(key: str, body: KeyUpdate):
    enc = encrypt(body.value)
    res = await get_db().api_configs.update_one(
        {"key": key.lower()},
        {"$set": {"encryptedValue": enc, "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Key not found", "NOT_FOUND", 404)
    await refresh_cache()
    return ok({"updated": True})


@router.post("/{key}/test")
async def test_key(key: str):
    db = get_db()
    rec = await db.api_configs.find_one({"key": key.lower()}, {"_id": 0})
    if not rec:
        raise APIError("Key not configured", "NOT_FOUND", 404)
    import time
    t0 = time.perf_counter()
    success, message = False, "Not implemented for this service"
    try:
        k = key.lower()
        if k == "openai":
            resp = await chat_completion(
                "Reply with the word READY only.", "Ping?",
                model="gpt-4o-mini")
            success = bool(resp) and not resp.startswith("[LLM unavailable")
            message = resp[:200]
        elif k == "sendgrid":
            from services import email as _e
            r = await _e.test_sendgrid()
            success, message = r.get("success", False), r.get("message", "")
        elif k.startswith("r2"):
            from services import storage as _s
            r = await _s.test_r2()
            success, message = r["success"], r["message"]
        elif k == "perplexity":
            from services import ai_visibility as _av
            r = await _av.test_perplexity()
            success, message = r["success"], r["message"]
        elif k == "anthropic":
            from services import ai_visibility as _av
            r = await _av.test_anthropic()
            success, message = r["success"], r["message"]
        elif k == "gemini":
            from services import ai_visibility as _av
            r = await _av.test_gemini()
            success, message = r["success"], r["message"]
        elif k.startswith("google"):
            from services import gsc as _gsc
            r = await _gsc.test_gsc()
            success, message = r["success"], r["message"]
        elif k.startswith("lemonsqueezy"):
            success, message = True, "LemonSqueezy store info fetched (MOCK)"
        elif k == "dataforseo":
            await mocks.keyword_research(["test"])
            success, message = True, "DataForSEO balance fetched (MOCK)"
        elif k == "resend":
            success, message = True, "Resend removed — use SendGrid"
        else:
            success, message = True, "Connectivity OK (mock)"
    except Exception as e:
        success, message = False, str(e)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    await db.api_configs.update_one(
        {"key": key.lower()},
        {"$set": {"lastTestedAt": utcnow_iso(),
                  "testStatus": "SUCCESS" if success else "FAILED"}})
    return ok({"success": success, "message": message,
               "latency_ms": latency_ms})
