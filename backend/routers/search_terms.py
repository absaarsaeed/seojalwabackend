"""Search terms / keyword research routes."""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from core.database import get_db
from core.dependencies import get_current_user
from core.response import APIError, created, ok
from core.security import utcnow_iso
from services import mocks

router = APIRouter(prefix="/search-terms", tags=["search-terms"])


class CreateReq(BaseModel):
    siteId: str
    terms: list[str]


class AiSuggestReq(BaseModel):
    siteId: str


@router.get("")
async def list_terms(siteId: str, user=Depends(get_current_user)):
    rows = await get_db().search_terms.find(
        {"userId": user["id"], "siteId": siteId},
        {"_id": 0}).sort("createdAt", -1).to_list(500)
    return ok(rows)


@router.post("")
async def add_terms(body: CreateReq, user=Depends(get_current_user)):
    db = get_db()
    research = await mocks.keyword_research(body.terms)
    research_map = {r["term"]: r for r in research}
    docs = []
    for t in body.terms:
        r = research_map.get(t, {})
        docs.append({
            "id": str(uuid.uuid4()), "siteId": body.siteId,
            "userId": user["id"], "term": t,
            "source": "USER_ADDED", "status": "PENDING",
            "monthlySearchVolume": r.get("monthlySearchVolume"),
            "difficulty": r.get("difficulty"),
            "createdAt": utcnow_iso(),
        })
    if docs:
        await db.search_terms.insert_many([dict(d) for d in docs])
    return created({"created": len(docs), "terms": docs})


@router.delete("/{term_id}")
async def remove_term(term_id: str, user=Depends(get_current_user)):
    res = await get_db().search_terms.delete_one(
        {"id": term_id, "userId": user["id"]})
    if res.deleted_count == 0:
        raise APIError("Term not found", "NOT_FOUND", 404)
    return ok({"deleted": True})


@router.post("/ai-suggest")
async def ai_suggest(body: AiSuggestReq, user=Depends(get_current_user)):
    db = get_db()
    site = await db.sites.find_one({"id": body.siteId, "userId": user["id"]},
                                   {"_id": 0})
    if not site:
        raise APIError("Site not found", "NOT_FOUND", 404)
    # TODO: feed real competitor pages + LLM call. For now use llm.chat
    from services.llm import chat_completion
    sys = "You are an SEO topic ideation assistant. Return 10 topic ideas as a newline list."
    prompt = f"Suggest 10 SEO topics for site {site.get('name')} ({site.get('url')})."
    raw = await chat_completion(sys, prompt)
    ideas = [line.strip("-• 0123456789.").strip()
             for line in raw.splitlines() if line.strip()][:10]
    docs = []
    for t in ideas:
        if not t:
            continue
        docs.append({
            "id": str(uuid.uuid4()), "siteId": body.siteId,
            "userId": user["id"], "term": t,
            "source": "AI_SUGGESTED", "status": "PENDING",
            "monthlySearchVolume": None, "difficulty": None,
            "createdAt": utcnow_iso(),
        })
    if docs:
        await db.search_terms.insert_many([dict(d) for d in docs])
    return ok({"suggested": len(docs), "terms": docs})
