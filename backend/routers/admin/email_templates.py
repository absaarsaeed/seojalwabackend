"""Admin: editable email templates."""
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, EmailStr

from core.audit import log_action
from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok
from core.security import utcnow_iso
from services import email as email_service
from services.email_templates import (
    SEED_TEMPLATES, invalidate_cache, render_template, seed_templates,
)

router = APIRouter(prefix="/admin/email-templates",
                   tags=["admin-email-templates"],
                   dependencies=[Depends(get_admin_session)])


class TemplateUpdate(BaseModel):
    subject: Optional[str] = None
    htmlBody: Optional[str] = None
    isActive: Optional[bool] = None
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    variables: Optional[list[str]] = None


class TestSendReq(BaseModel):
    testEmail: EmailStr


@router.get("")
async def list_templates():
    # Ensure seed has run at least once.
    await seed_templates()
    rows = await get_db().email_templates.find({}, {"_id": 0}).sort(
        "key", 1).to_list(200)
    return ok(rows)


@router.get("/{key}")
async def get_template_doc(key: str):
    doc = await get_db().email_templates.find_one({"key": key}, {"_id": 0})
    if not doc:
        # Auto-seed if missing
        await seed_templates()
        doc = await get_db().email_templates.find_one({"key": key},
                                                       {"_id": 0})
    if not doc:
        raise APIError("Template not found", "NOT_FOUND", 404)
    return ok(doc)


@router.put("/{key}")
async def update_template(key: str, body: TemplateUpdate, request: Request):
    db = get_db()
    existing = await db.email_templates.find_one({"key": key}, {"_id": 0})
    if not existing:
        await seed_templates()
        existing = await db.email_templates.find_one({"key": key},
                                                      {"_id": 0})
    if not existing:
        raise APIError("Template not found", "NOT_FOUND", 404)

    upd = body.model_dump(exclude_none=True)
    if not upd:
        raise APIError("Nothing to update", "VALIDATION_ERROR", 422)
    upd["updatedAt"] = utcnow_iso()
    await db.email_templates.update_one({"key": key}, {"$set": upd})
    invalidate_cache(key)

    await log_action(
        "EMAIL_TEMPLATE_UPDATED", target_type="email_template",
        target_id=key,
        ip_address=(request.client.host if request.client else ""),
        changes={k: {"from": existing.get(k), "to": v}
                 for k, v in upd.items() if k != "updatedAt"})

    fresh = await db.email_templates.find_one({"key": key}, {"_id": 0})
    return ok(fresh, "Template updated")


@router.post("/{key}/test")
async def test_send(key: str, body: TestSendReq):
    # Build sample variables from the template's declared variables list
    tpl = await get_db().email_templates.find_one({"key": key}, {"_id": 0})
    if not tpl:
        raise APIError("Template not found", "NOT_FOUND", 404)
    sample_vars = {v: f"[{v}]" for v in (tpl.get("variables") or [])}
    rendered = await render_template(key, sample_vars)
    if not rendered:
        raise APIError("Template inactive or missing",
                       "TEMPLATE_INACTIVE", 422)
    res = await email_service.send_email(
        body.testEmail, rendered["subject"], rendered["html"],
        template=f"test:{key}")
    return ok({"sent": res.get("success"),
               "provider": res.get("provider"),
               "error": res.get("error")})


@router.post("/seed")
async def reseed():
    inserted = await seed_templates()
    return ok({"inserted": inserted,
               "totalSeeds": len(SEED_TEMPLATES)})
