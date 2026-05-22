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
    """Always return a valid structure — never null/404. When no scores
    exist yet, every component is 0 and a friendly onboarding message
    is included."""
    db = get_db()
    history_docs = await db.growth_scores.find(
        {"siteId": siteId, "userId": user["id"]},
        {"_id": 0}).sort("calculatedAt", -1).limit(8).to_list(8)

    history = [{
        "score": h.get("score", 0),
        "calculatedAt": h.get("calculatedAt", h.get("createdAt", "")),
    } for h in reversed(history_docs)]  # oldest → newest for chart

    if not history_docs:
        return ok({
            "score": 0,
            "breakdown": {
                "aiVisibility": 0, "seoContent": 0,
                "socialConsistency": 0, "trafficTrend": 0,
            },
            "history": [],
            "trend": "stable",
            "change": 0,
            "latest": None,  # legacy
            "message": ("Run your first AI scan to start building "
                        "your score"),
        })

    latest = history_docs[0]
    prev = history_docs[1] if len(history_docs) > 1 else None
    change = (latest.get("score", 0) - (prev or {}).get("score", 0)) \
        if prev else 0
    trend = "up" if change > 2 else ("down" if change < -2 else "stable")

    return ok({
        "score": latest.get("score", 0),
        "breakdown": {
            "aiVisibility": latest.get("aiVisibilityComponent", 0),
            "seoContent": latest.get("seoContentComponent", 0),
            "socialConsistency": latest.get(
                "socialConsistencyComponent", 0),
            "trafficTrend": latest.get("trafficTrendComponent", 0),
        },
        "history": history,
        "trend": trend,
        "change": change,
        "latest": latest,  # legacy
    })


@router.post("/calculate")
async def calculate(body: CalcReq, user=Depends(get_current_user)):
    site = await get_db().sites.find_one(
        {"id": body.siteId, "userId": user["id"]}, {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    rec = await jobs.run_growth_score(body.siteId, user["id"])
    return ok({"score": rec["score"], "breakdown": rec})
