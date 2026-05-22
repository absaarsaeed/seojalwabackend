"""Admin: plans CRUD."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, created, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/admin/plans", tags=["admin-plans"],
                   dependencies=[Depends(get_admin_session)])


class PlanBody(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    monthlyPrice: Optional[float] = None
    annualPrice: Optional[float] = None
    description: Optional[str] = None
    articlesPerMonth: Optional[int] = None
    socialPostsPerMonth: Optional[int] = None
    aiScansPerMonth: Optional[int] = None
    teamSeats: Optional[int] = None
    cmsConnections: Optional[int] = None
    websiteConnections: Optional[int] = None
    brandVoiceModel: Optional[bool] = None
    competitorComparison: Optional[bool] = None
    prioritySupport: Optional[bool] = None
    whiteLabel: Optional[bool] = None
    gscConnection: Optional[bool] = None
    isActive: Optional[bool] = None
    isFree: Optional[bool] = None
    sortOrder: Optional[int] = None
    order: Optional[int] = None
    features: Optional[dict] = None  # nested feature toggle map


def _sync_features_flat(doc: dict) -> dict:
    """Mirror the nested `features` map into legacy flat top-level keys so
    older readers (limit checks, dashboards) keep working."""
    feats = doc.get("features") or {}
    for key, meta in feats.items():
        if not isinstance(meta, dict):
            continue
        # Even when disabled, store the underlying value as 0 / False
        # so the legacy reader gets a sensible default.
        if meta.get("enabled"):
            doc[key] = meta.get("value")
        else:
            v = meta.get("value")
            doc[key] = 0 if isinstance(v, (int, float)) else False
    if "order" in doc and "sortOrder" not in doc:
        doc["sortOrder"] = doc["order"] * 10
    if "sortOrder" in doc and "order" not in doc:
        doc["order"] = doc["sortOrder"] // 10
    return doc


@router.get("")
async def list_plans():
    rows = await get_db().plans.find({}, {"_id": 0}).sort(
        [("order", 1), ("sortOrder", 1)]).to_list(100)
    for r in rows:
        if "websiteConnections" not in r and "cmsConnections" in r:
            r["websiteConnections"] = r["cmsConnections"]
        if "cmsConnections" not in r and "websiteConnections" in r:
            r["cmsConnections"] = r["websiteConnections"]
    return ok(rows)


@router.post("")
async def create_plan(body: PlanBody):
    doc = body.model_dump(exclude_none=True)
    # Master prompt Part 11 — keep cmsConnections/websiteConnections in sync
    if "websiteConnections" in doc and "cmsConnections" not in doc:
        doc["cmsConnections"] = doc["websiteConnections"]
    elif "cmsConnections" in doc and "websiteConnections" not in doc:
        doc["websiteConnections"] = doc["cmsConnections"]
    doc = _sync_features_flat(doc)
    doc["id"] = str(uuid.uuid4())
    doc.setdefault("isActive", True)
    doc.setdefault("sortOrder", 100)
    doc.setdefault("order", doc["sortOrder"] // 10)
    doc.setdefault("isFree", False)
    doc["createdAt"] = utcnow_iso()
    doc["updatedAt"] = utcnow_iso()
    await get_db().plans.insert_one(dict(doc))
    doc.pop("_id", None)
    return created(doc)


@router.put("/{plan_id}")
async def update_plan(plan_id: str, body: PlanBody):
    upd = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    if "websiteConnections" in upd and "cmsConnections" not in upd:
        upd["cmsConnections"] = upd["websiteConnections"]
    elif "cmsConnections" in upd and "websiteConnections" not in upd:
        upd["websiteConnections"] = upd["cmsConnections"]

    # Phase 2 bug fix — when only a subset of `features` is provided,
    # merge it into the existing map instead of $set-ing the whole dict
    # (which destructively replaces every other feature).
    features_patch = upd.pop("features", None)
    upd = _sync_features_flat({**upd, **({"features": features_patch}
                                           if features_patch else {})})
    # _sync_features_flat may have re-added features into upd — drop it
    # so we can apply dot-notation updates instead.
    upd.pop("features", None)
    upd["updatedAt"] = utcnow_iso()

    set_payload: dict = {**upd}
    if features_patch:
        for k, v in features_patch.items():
            set_payload[f"features.{k}"] = v

    res = await get_db().plans.update_one(
        {"id": plan_id}, {"$set": set_payload})
    if res.matched_count == 0:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    fresh = await get_db().plans.find_one({"id": plan_id}, {"_id": 0})
    return ok({"updated": True, "plan": fresh})


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str):
    res = await get_db().plans.update_one(
        {"id": plan_id}, {"$set": {"isActive": False,
                                    "updatedAt": utcnow_iso()}})
    if res.matched_count == 0:
        raise APIError("Plan not found", "NOT_FOUND", 404)
    return ok({"deleted": True})
