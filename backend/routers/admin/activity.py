"""Admin: per-user activity log."""
from typing import Optional

from fastapi import APIRouter, Depends

from core.dependencies import get_admin_session
from core.response import ok, paginate
from services.activity import list_activity

router = APIRouter(prefix="/admin/users", tags=["admin-activity"],
                   dependencies=[Depends(get_admin_session)])


@router.get("/{user_id}/activity-log")
async def admin_user_activity(user_id: str, page: int = 1, limit: int = 50,
                              action: Optional[str] = None):
    rows, total = await list_activity(user_id, page=page, limit=limit,
                                       action=action)
    return ok(rows, pagination=paginate(rows, total, page, limit))
