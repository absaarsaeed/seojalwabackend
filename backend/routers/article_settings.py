"""Article settings (per site)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, ok
from core.security import utcnow_iso

router = APIRouter(prefix="/article-settings", tags=["article-settings"])


DEFAULTS = {
    "autoPublish": True, "delayPublishing": False,
    "includeHeroImages": True, "includeYoutubeVideos": False,
    "includeInfographics": True, "includeKeyTakeaways": True,
    "includeTableOfContents": True, "addExternalLinks": True,
    "articleLength": "WORDS_2000", "publishingFrequency": 5,
    "writingLanguage": "English", "writingInstructions": "",
    "websiteTitle": "", "websiteDescription": "",
    "targetCountry": "Worldwide", "targetCity": "",
    "whatYouSell": "", "whatYouDontSell": "",
    "imageryPrompt": "",
}


class SettingsUpdate(BaseModel):
    autoPublish: Optional[bool] = None
    delayPublishing: Optional[bool] = None
    includeHeroImages: Optional[bool] = None
    includeYoutubeVideos: Optional[bool] = None
    includeInfographics: Optional[bool] = None
    includeKeyTakeaways: Optional[bool] = None
    includeTableOfContents: Optional[bool] = None
    addExternalLinks: Optional[bool] = None
    articleLength: Optional[str] = None
    publishingFrequency: Optional[int] = None
    writingLanguage: Optional[str] = None
    writingInstructions: Optional[str] = None
    websiteTitle: Optional[str] = None
    websiteDescription: Optional[str] = None
    targetCountry: Optional[str] = None
    targetCity: Optional[str] = None
    whatYouSell: Optional[str] = None
    whatYouDontSell: Optional[str] = None
    imageryPrompt: Optional[str] = None


@router.get("/{site_id}")
async def get_settings(site_id: str, user=Depends(get_current_user)):
    doc = await get_db().article_settings.find_one(
        {"siteId": site_id, "userId": user["id"]}, {"_id": 0})
    if not doc:
        return ok({"siteId": site_id, "userId": user["id"], **DEFAULTS})
    return ok(doc)


@router.put("/{site_id}")
async def upsert_settings(site_id: str, body: SettingsUpdate,
                          user=Depends(get_current_user)):
    db = get_db()
    fields = {k: v for k, v in body.model_dump(exclude_none=True).items()}
    fields["updatedAt"] = utcnow_iso()
    existing = await db.article_settings.find_one(
        {"siteId": site_id, "userId": user["id"]}, {"_id": 0})
    if existing:
        await db.article_settings.update_one(
            {"siteId": site_id, "userId": user["id"]}, {"$set": fields})
    else:
        import uuid
        doc = {"id": str(uuid.uuid4()), "siteId": site_id,
               "userId": user["id"], **DEFAULTS, **fields,
               "createdAt": utcnow_iso()}
        await db.article_settings.insert_one(dict(doc))
    doc = await db.article_settings.find_one(
        {"siteId": site_id, "userId": user["id"]}, {"_id": 0})
    return ok(doc, "Settings saved")
