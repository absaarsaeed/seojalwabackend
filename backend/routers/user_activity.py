"""User's own activity log."""
from typing import Optional

from fastapi import APIRouter, Depends

from core.dependencies import get_current_user
from core.response import ok, paginate
from services.activity import list_activity

router = APIRouter(prefix="/user/activity", tags=["user-activity"])


@router.get("")
async def my_activity(page: int = 1, limit: int = 50,
                      action: Optional[str] = None,
                      user=Depends(get_current_user)):
    rows, total = await list_activity(user["id"], page=page, limit=limit,
                                       action=action)
    return ok(rows, pagination=paginate(rows, total, page, limit))
