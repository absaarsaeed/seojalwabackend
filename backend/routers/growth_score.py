"""Growth score routes."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from services import jobs

router = APIRouter(prefix="/growth-score", tags=["growth-score"])


class CalcReq(BaseModel):
    siteId: str


@router.get("")
async def get_score(siteId: str, user=Depends(get_current_user)):
    db = get_db()
    latest = await db.growth_scores.find_one(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}, sort=[("calculatedAt", -1)])
    history = await db.growth_scores.find(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}).sort("calculatedAt", -1).limit(30).to_list(30)
    return ok({"latest": latest, "history": history})


@router.post("/calculate")
async def calculate(body: CalcReq, user=Depends(get_current_user)):
    site = await get_db().sites.find_one(
        {"id": body.siteId, "userId": user["id"]}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    rec = await jobs.run_growth_score(body.siteId, user["id"])
    return ok({"score": rec["score"], "breakdown": rec})
