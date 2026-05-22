"""Admin: audit log listing."""
from typing import Optional

from fastapi import APIRouter, Depends

from core.audit import list_actions
from core.dependencies import get_admin_session
from core.response import ok, paginate

router = APIRouter(prefix="/admin/audit-log", tags=["admin-audit"],
                   dependencies=[Depends(get_admin_session)])


@router.get("")
async def list_audit(page: int = 1, limit: int = 50,
                     action: Optional[str] = None,
                     target_id: Optional[str] = None):
    rows, total = await list_actions(page=page, limit=limit,
                                     action=action, target_id=target_id)
    return ok(rows, pagination=paginate(rows, total, page, limit))


@router.get("/{entry_id}")
async def get_audit_entry(entry_id: str):
    """Phase 3 FIX 8 — full audit entry incl. `changes` dict for diff UI."""
    from core.database import get_db
    from core.response import APIError
    entry = await get_db().admin_audit_log.find_one(
        {"id": entry_id}, {"_id": 0})
    if not entry:
        raise APIError("Audit entry not found", "NOT_FOUND", 404)
    return ok(entry)
