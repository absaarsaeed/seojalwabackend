"""Idempotent seed: 3 default plans, admin credentials, default ApiConfig rows."""
import asyncio
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from core.database import get_db  # noqa: E402
from core.security import hash_password, utcnow_iso  # noqa: E402
from services.api_catalog import CATALOG  # noqa: E402


DEFAULT_PLANS = [
    {
        "name": "Free", "slug": "free",
        "monthlyPrice": 0, "annualPrice": 0,
        "isFree": True, "order": 0, "sortOrder": 0,
        "description": "Get started with 3 articles/month — free forever.",
        "articlesPerMonth": 3, "socialPostsPerMonth": 0,
        "aiScansPerMonth": 0, "teamSeats": 1,
        "websiteConnections": 1, "cmsConnections": 1,
        "brandVoiceModel": False, "competitorComparison": False,
        "prioritySupport": False, "whiteLabel": False,
        "gscConnection": True,
        "isActive": True,
        "features": {
            "articlesPerMonth": {"enabled": True, "value": 3},
            "websiteConnections": {"enabled": True, "value": 1},
            "gscConnection": {"enabled": True, "value": True},
            "aiScansPerMonth": {"enabled": False, "value": 0},
            "socialPostsPerMonth": {"enabled": False, "value": 0},
            "teamSeats": {"enabled": False, "value": 1},
            "whiteLabel": {"enabled": True, "value": False},
            "prioritySupport": {"enabled": False, "value": False},
        },
    },
    {
        "name": "Starter", "slug": "starter",
        "monthlyPrice": 49, "annualPrice": 39,
        "isFree": False, "order": 1, "sortOrder": 10,
        "description": "Perfect for small teams getting started.",
        "articlesPerMonth": 20, "socialPostsPerMonth": 0,
        "aiScansPerMonth": 5, "teamSeats": 1,
        "websiteConnections": 1, "cmsConnections": 1,
        "brandVoiceModel": True, "competitorComparison": False,
        "prioritySupport": False, "whiteLabel": False,
        "gscConnection": True, "isActive": True,
        "features": {
            "articlesPerMonth": {"enabled": True, "value": 20},
            "websiteConnections": {"enabled": True, "value": 1},
            "gscConnection": {"enabled": True, "value": True},
            "aiScansPerMonth": {"enabled": True, "value": 5},
            "socialPostsPerMonth": {"enabled": False, "value": 0},
            "teamSeats": {"enabled": False, "value": 1},
            "whiteLabel": {"enabled": True, "value": False},
            "prioritySupport": {"enabled": False, "value": False},
        },
    },
    {
        "name": "Growth", "slug": "growth",
        "monthlyPrice": 99, "annualPrice": 79,
        "isFree": False, "order": 2, "sortOrder": 20,
        "description": "Best for growing brands.",
        "articlesPerMonth": 60, "socialPostsPerMonth": 0,
        "aiScansPerMonth": 20, "teamSeats": 3,
        "websiteConnections": 3, "cmsConnections": 3,
        "brandVoiceModel": True, "competitorComparison": True,
        "prioritySupport": True, "whiteLabel": True,
        "gscConnection": True, "isActive": True,
        "features": {
            "articlesPerMonth": {"enabled": True, "value": 60},
            "websiteConnections": {"enabled": True, "value": 3},
            "gscConnection": {"enabled": True, "value": True},
            "aiScansPerMonth": {"enabled": True, "value": 20},
            "socialPostsPerMonth": {"enabled": False, "value": 0},
            "teamSeats": {"enabled": True, "value": 3},
            "whiteLabel": {"enabled": True, "value": True},
            "prioritySupport": {"enabled": True, "value": True},
        },
    },
    {
        "name": "Agency", "slug": "agency",
        "monthlyPrice": 199, "annualPrice": 159,
        "isFree": False, "order": 3, "sortOrder": 30,
        "description": "Everything for agencies and large teams.",
        "articlesPerMonth": 150, "socialPostsPerMonth": 0,
        "aiScansPerMonth": 50, "teamSeats": 10,
        "websiteConnections": 10, "cmsConnections": 10,
        "brandVoiceModel": True, "competitorComparison": True,
        "prioritySupport": True, "whiteLabel": True,
        "gscConnection": True, "isActive": True,
        "features": {
            "articlesPerMonth": {"enabled": True, "value": 150},
            "websiteConnections": {"enabled": True, "value": 10},
            "gscConnection": {"enabled": True, "value": True},
            "aiScansPerMonth": {"enabled": True, "value": 50},
            "socialPostsPerMonth": {"enabled": False, "value": 0},
            "teamSeats": {"enabled": True, "value": 10},
            "whiteLabel": {"enabled": True, "value": True},
            "prioritySupport": {"enabled": True, "value": True},
        },
    },
]


async def _migrate_plan_field_rename(db):
    """Master prompt Part 11 — rename `cmsConnections` → `websiteConnections`.

    Idempotent: only updates plans that have `cmsConnections` but no
    `websiteConnections` field yet. Keeps the legacy key around for
    backward compatibility with any existing frontend code that still
    reads it.
    """
    async for p in db.plans.find(
            {"cmsConnections": {"$exists": True},
             "websiteConnections": {"$exists": False}}, {"_id": 0}):
        await db.plans.update_one(
            {"id": p["id"]},
            {"$set": {"websiteConnections": p.get("cmsConnections"),
                       "updatedAt": utcnow_iso()}})


async def run_seed():
    db = get_db()

    # Plans — insert if missing, ALSO backfill new fields (slug, features,
    # isFree, order) on existing plan docs from earlier seeds.
    for p in DEFAULT_PLANS:
        existing = await db.plans.find_one({"name": p["name"]}, {"_id": 0})
        if not existing:
            doc = {"id": str(uuid.uuid4()), **p,
                   "createdAt": utcnow_iso(), "updatedAt": utcnow_iso()}
            await db.plans.insert_one(dict(doc))
        else:
            # Backfill — only add the keys that don't exist yet
            patch = {}
            for k in ("slug", "isFree", "order", "features",
                       "gscConnection"):
                if k not in existing and k in p:
                    patch[k] = p[k]
            if patch:
                patch["updatedAt"] = utcnow_iso()
                await db.plans.update_one(
                    {"id": existing["id"]}, {"$set": patch})

    # Migration: rename cmsConnections → websiteConnections on legacy plans
    await _migrate_plan_field_rename(db)

    # Admin credentials
    existing_admin = await db.admin_credentials.find_one(
        {"id": "admin"}, {"_id": 0})
    if not existing_admin:
        await db.admin_credentials.insert_one({
            "id": "admin",
            "username": os.environ.get("ADMIN_USERNAME", "jalwa"),
            "passwordHash": hash_password(
                os.environ.get("ADMIN_PASSWORD", "jalwaadmin")),
            "createdAt": utcnow_iso(),
        })

    # Default API config rows for every catalogue entry (empty values, inactive)
    for entry in CATALOG:
        existing = await db.api_configs.find_one({"key": entry["key"]},
                                                  {"_id": 0})
        if not existing:
            await db.api_configs.insert_one({
                "id": str(uuid.uuid4()), "key": entry["key"],
                "encryptedValue": "", "isActive": False,
                "testStatus": "UNTESTED",
                "updatedAt": utcnow_iso(),
            })


if __name__ == "__main__":
    asyncio.run(run_seed())
    print("seed complete")
