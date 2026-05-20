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
