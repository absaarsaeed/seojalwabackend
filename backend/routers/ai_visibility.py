"""AI Visibility — scans, competitors, public simulator."""
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.rate_limit import rate_limit
from core.response import APIError, ok
from core.security import utcnow_iso
from services import jobs, mocks

router = APIRouter(prefix="/ai-visibility", tags=["ai-visibility"])


class ScanReq(BaseModel):
    siteId: str


class SimulateReq(BaseModel):
    query: str


@router.get("/scans")
async def list_scans(siteId: str, limit: int = 20,
                     user=Depends(get_current_user)):
    rows = await get_db().ai_visibility_scans.find(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0, "rawResults": 0}).sort("scannedAt", -1).limit(limit).to_list(limit)
    return ok(rows)


@router.get("/latest")
async def latest_scan(siteId: str, user=Depends(get_current_user)):
    row = await get_db().ai_visibility_scans.find_one(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}, sort=[("scannedAt", -1)])
    if not row:
        return ok(None, "No scans yet")
    return ok(row)


@router.post("/scan")
async def trigger_scan(body: ScanReq, bg: BackgroundTasks,
                       user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one({"id": body.siteId, "userId": user["id"]},
                                   {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    job_id = await jobs.create_job("ai_visibility_scan",
                                   {"siteId": body.siteId})
    bg.add_task(jobs.run_ai_visibility_scan, job_id, body.siteId, user["id"])
    return ok({"jobId": job_id, "status": "queued"})


@router.get("/scan/{job_id}")
async def scan_status(job_id: str, user=Depends(get_current_user)):
    job = await get_db().jobs.find_one({"id": job_id}, {"_id": 0})
    if not job:
        raise APIError("Job not found", "NOT_FOUND", 404)
    return ok(job)


@router.get("/competitors")
async def competitors(siteId: str, user=Depends(get_current_user)):
    rows = await get_db().competitors.find(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}).to_list(100)
    return ok(rows)


@router.post("/simulate",
             dependencies=[Depends(rate_limit("ai-simulate", 5, 3600))])
async def simulate(body: SimulateReq):
    """Public endpoint, rate limited 5/hr per IP."""
    results = await mocks.query_ai_models(body.query, [body.query])
    return ok({"query": body.query, "results": results})
