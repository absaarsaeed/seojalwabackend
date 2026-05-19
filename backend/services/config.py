"""ConfigService — DB-first, env-fallback, in-memory 5-min cache for API keys.

Storage shape:
    api_configs document = {
        "id": uuid,
        "key": "openai",         # service slug from api_catalog
        "encryptedValue": "<Fernet-encrypted JSON of fields dict>",
        "isActive": true,
        "lastTestedAt": iso-string,
        "testStatus": "SUCCESS" | "FAILED" | "UNTESTED",
        "updatedAt": iso-string,
    }

Public methods:
    await get_fields(key) -> dict        # all fields for a service
    await get_value(key, field="api_key") -> str
    invalidate(key)
    set_fields(key, fields)  # used by admin save endpoint
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from core.database import get_db
from core.encryption import decrypt, encrypt
from core.security import utcnow_iso
from services.api_catalog import CATALOG_BY_KEY, all_keys

logger = logging.getLogger("jalwa.config")

_CACHE_TTL = 300  # 5 minutes


class ConfigService:
    def __init__(self):
        self._cache: dict[str, dict] = {}
        self._cache_time: dict[str, float] = {}

    # --------------------------------------------------------------- read
    async def get_fields(self, key: str) -> dict:
        """Return the full dict of fields for a service.

        Priority: DB (decrypted) > env-var fallback (per env_map). Returns
        an empty dict if nothing is configured.
        """
        key = key.lower()
        if self._fresh(key):
            return self._cache[key]

        # 1) Database
        db = get_db()
        rec = await db.api_configs.find_one(
            {"key": key, "isActive": {"$ne": False}}, {"_id": 0})
        fields: dict[str, str] = {}
        if rec and rec.get("encryptedValue"):
            plain = decrypt(rec["encryptedValue"]) or ""
            if plain:
                try:
                    parsed = json.loads(plain)
                    if isinstance(parsed, dict):
                        fields.update({k: str(v) for k, v in parsed.items()
                                       if v not in (None, "")})
                except json.JSONDecodeError:
                    # Legacy single-string value — assume it's `api_key`
                    fields["api_key"] = plain

        # 2) Env-var fallback for any field still missing
        entry = CATALOG_BY_KEY.get(key)
        if entry:
            for field_name, env_name in entry.get("env_map", {}).items():
                if not fields.get(field_name):
                    val = os.environ.get(env_name, "")
                    if val:
                        fields[field_name] = val

        self._cache[key] = fields
        self._cache_time[key] = time.time()
        return fields

    async def get_value(self, key: str, field: str = "api_key") -> str:
        """Convenience: return a single field of a service config."""
        return (await self.get_fields(key)).get(field, "")

    # --------------------------------------------------------------- write
    async def set_fields(self, key: str, fields: dict[str, Any]) -> dict:
        """Encrypt and upsert. Returns the saved record (without ciphertext)."""
        import uuid

        key = key.lower()
        # Only persist known fields per catalogue (defence in depth)
        entry = CATALOG_BY_KEY.get(key)
        if entry:
            allowed = {f["name"] for f in entry["fields"]}
            fields = {k: v for k, v in fields.items() if k in allowed}

        clean = {k: ("" if v is None else str(v)) for k, v in fields.items()}
        encrypted = encrypt(json.dumps(clean))

        db = get_db()
        existing = await db.api_configs.find_one({"key": key}, {"_id": 0})
        now = utcnow_iso()
        if existing:
            await db.api_configs.update_one(
                {"key": key},
                {"$set": {"encryptedValue": encrypted, "isActive": True,
                          "updatedAt": now}})
        else:
            await db.api_configs.insert_one({
                "id": str(uuid.uuid4()), "key": key,
                "encryptedValue": encrypted, "isActive": True,
                "testStatus": "UNTESTED",
                "updatedAt": now,
            })
        self.invalidate(key)
        rec = await db.api_configs.find_one({"key": key}, {"_id": 0,
                                                           "encryptedValue": 0})
        return rec or {"key": key, "isActive": True, "updatedAt": now}

    # --------------------------------------------------------- cache control
    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._cache.clear()
            self._cache_time.clear()
        else:
            self._cache.pop(key.lower(), None)
            self._cache_time.pop(key.lower(), None)

    def _fresh(self, key: str) -> bool:
        t = self._cache_time.get(key)
        return bool(t and (time.time() - t < _CACHE_TTL) and key in self._cache)


# Global singleton used everywhere
config_service = ConfigService()


# ------------------------- helper for masking field values in admin UI
def mask_value(value: str | None) -> str:
    if not value:
        return ""
    if len(value) < 8:
        return "••••••••"
    return "••••••••••••" + value[-4:]


def build_admin_view(key: str, db_rec: dict | None,
                     fields_values: dict) -> dict:
    """Merge catalogue metadata with DB status + masked field values."""
    entry = CATALOG_BY_KEY.get(key.lower())
    if not entry:
        return {"key": key, "error": "unknown service"}

    out_fields = []
    any_configured = False
    for f in entry["fields"]:
        raw = fields_values.get(f["name"], "")
        if raw:
            any_configured = True
        out_fields.append({
            "name": f["name"],
            "label": f["label"],
            "type": f.get("type", "password"),
            "placeholder": f.get("placeholder", ""),
            "required": f.get("required", False),
            "value": mask_value(raw),
            "isSet": bool(raw),
        })

    test_status = (db_rec or {}).get("testStatus", "UNTESTED")
    last_tested = (db_rec or {}).get("lastTestedAt")
    status = "not_connected"
    if entry.get("status_note"):
        status = entry["status_note"]
    elif any_configured and test_status == "SUCCESS":
        status = "connected"
    elif any_configured and test_status == "FAILED":
        status = "error"
    elif any_configured:
        status = "connected"  # configured but never tested → optimistic

    return {
        "key": entry["key"],
        "label": entry["label"],
        "section": entry["section"],
        "description": entry["description"],
        "fields": out_fields,
        "status": status,
        "last_tested": last_tested,
        "test_status": test_status.lower() if test_status else "untested",
        "instructions": entry["instructions"],
    }


__all__ = [
    "config_service", "mask_value", "build_admin_view",
    "all_keys", "CATALOG_BY_KEY",
]
