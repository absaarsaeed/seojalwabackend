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
        "name": "Starter", "monthlyPrice": 79, "annualPrice": 790,
        "description": "Perfect for small teams getting started.",
        "articlesPerMonth": 20, "socialPostsPerMonth": 60,
        "aiScansPerMonth": 4, "teamSeats": 2,
        "websiteConnections": 1, "cmsConnections": 1,
        "brandVoiceModel": True, "competitorComparison": False,
        "prioritySupport": False, "whiteLabel": False,
        "isActive": True, "sortOrder": 10,
    },
    {
        "name": "Growth", "monthlyPrice": 199, "annualPrice": 1990,
        "description": "Best for growing brands.",
        "articlesPerMonth": 60, "socialPostsPerMonth": 200,
        "aiScansPerMonth": 12, "teamSeats": 5,
        "websiteConnections": 3, "cmsConnections": 3,
        "brandVoiceModel": True, "competitorComparison": True,
        "prioritySupport": True, "whiteLabel": False,
        "isActive": True, "sortOrder": 20,
    },
    {
        "name": "Agency", "monthlyPrice": 499, "annualPrice": 4990,
        "description": "Everything for agencies and large teams.",
        "articlesPerMonth": 200, "socialPostsPerMonth": 1000,
        "aiScansPerMonth": 60, "teamSeats": 20,
        "websiteConnections": -1, "cmsConnections": -1,
        "brandVoiceModel": True, "competitorComparison": True,
        "prioritySupport": True, "whiteLabel": True,
        "isActive": True, "sortOrder": 30,
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

    # Plans
    for p in DEFAULT_PLANS:
        existing = await db.plans.find_one({"name": p["name"]}, {"_id": 0})
        if not existing:
            doc = {"id": str(uuid.uuid4()), **p,
                   "createdAt": utcnow_iso(), "updatedAt": utcnow_iso()}
            await db.plans.insert_one(dict(doc))

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
