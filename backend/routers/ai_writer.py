"""AI Writer — brand voice, content generation library."""
import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, created, ok, paginate
from core.security import utcnow_iso
from services import jobs, llm

router = APIRouter(tags=["ai-writer"])


class TrainReq(BaseModel):
    siteId: str
    contentSamples: Optional[list[str]] = None
    websiteUrl: Optional[str] = None


class GenerateReq(BaseModel):
    siteId: str
    type: str
    topic: str
    brief: Optional[str] = None
    targetKeyword: Optional[str] = None


class ScoreReq(BaseModel):
    siteId: str
    content: str


@router.get("/brand-voice/{site_id}")
async def get_voice(site_id: str, user=Depends(get_current_user)):
    doc = await get_db().brand_voices.find_one(
        {"siteId": site_id, "userId": user["id"]}, {"_id": 0})
    if not doc:
        return ok(None, "No brand voice trained yet")
    return ok(doc)


@router.post("/brand-voice/train")
async def train_voice(body: TrainReq, bg: BackgroundTasks,
                      user=Depends(get_current_user)):
    samples = body.contentSamples or []
    if body.websiteUrl and not samples:
        # TODO: scrape websiteUrl pages to build samples
        samples = [f"Sample content from {body.websiteUrl}"]
    if not samples:
        raise APIError("Provide contentSamples or websiteUrl", "INVALID", 400)
    job_id = await jobs.create_job("brand_voice_train", {"siteId": body.siteId})
    bg.add_task(jobs.run_brand_voice_training, job_id, body.siteId,
                user["id"], samples)
    return ok({"jobId": job_id, "status": "queued"})


@router.post("/content/generate")
async def generate_content(body: GenerateReq, user=Depends(get_current_user)):
    db = get_db()
    brand_voice = await db.brand_voices.find_one(
        {"siteId": body.siteId, "isActive": True}, {"_id": 0})
    type_to_system = {
        "BLOG_ARTICLE": "Write a blog article",
        "EMAIL": "Write a marketing email",
        "AD_COPY": "Write punchy ad copy (3 variants)",
        "SOCIAL_CAPTION": "Write a single social caption",
        "PRODUCT_DESCRIPTION": "Write a product description",
    }
    type_ = body.type.upper()
    if type_ not in type_to_system:
        raise APIError("Invalid type", "INVALID_TYPE", 400)
    system = type_to_system[type_] + " in the brand voice provided."
    prompt = (f"Topic: {body.topic}\nKeyword: {body.targetKeyword or 'N/A'}\n"
              f"Brief: {body.brief or 'N/A'}")
    content = await llm.chat_completion(system, prompt)
    voice_score = (await llm.score_against_voice(content, brand_voice))["score"]
    doc = {
        "id": str(uuid.uuid4()), "userId": user["id"], "siteId": body.siteId,
        "type": type_, "title": body.topic, "content": content,
        "voiceScore": voice_score, "wordCount": len(content.split()),
        "status": "DRAFT",
        "createdAt": utcnow_iso(), "updatedAt": utcnow_iso(),
    }
    await db.generated_content.insert_one(dict(doc))
    doc.pop("_id", None)
    return created(doc)


@router.post("/content/voice-score")
async def voice_score(body: ScoreReq, user=Depends(get_current_user)):
    brand_voice = await get_db().brand_voices.find_one(
        {"siteId": body.siteId, "userId": user["id"]}, {"_id": 0})
    res = await llm.score_against_voice(body.content, brand_voice)
    return ok(res)


@router.get("/content/library")
async def library(siteId: Optional[str] = None, type: Optional[str] = None,
                  page: int = 1, limit: int = 20,
                  user=Depends(get_current_user)):
    db = get_db()
    q: dict = {"userId": user["id"]}
    if siteId:
        q["siteId"] = siteId
    if type:
        q["type"] = type.upper()
    total = await db.generated_content.count_documents(q)
    rows = await db.generated_content.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.delete("/content/{cid}")
async def delete_content(cid: str, user=Depends(get_current_user)):
    res = await get_db().generated_content.delete_one(
        {"id": cid, "userId": user["id"]})
    if res.deleted_count == 0:
        raise APIError("Not found", "NOT_FOUND", 404)
    return ok({"deleted": True})
