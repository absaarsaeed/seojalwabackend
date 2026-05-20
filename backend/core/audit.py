"""Admin audit log helpers + collection writer."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from core.database import get_db
from core.security import utcnow_iso

logger = logging.getLogger("jalwa.audit")


async def log_action(
    action: str,
    *,
    target_type: str = "",
    target_id: str = "",
    admin_username: str = "admin",
    ip_address: str = "",
    changes: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> str:
    """Insert an admin audit log entry. Returns the generated id."""
    rec_id = str(uuid.uuid4())
    rec = {
        "id": rec_id,
        "action": action,
        "targetType": target_type,
        "targetId": target_id,
        "adminUsername": admin_username,
        "ipAddress": ip_address,
        "changes": changes or {},
        "metadata": metadata or {},
        "createdAt": utcnow_iso(),
    }
    try:
        await get_db().admin_audit_log.insert_one(dict(rec))
    except Exception as e:  # noqa: BLE001
        logger.warning("audit log insert failed (%s): %s", action, e)
    return rec_id


async def list_actions(
    page: int = 1,
    limit: int = 50,
    action: Optional[str] = None,
    target_id: Optional[str] = None,
) -> tuple[list[dict], int]:
    db = get_db()
    q: dict[str, Any] = {}
    if action:
        q["action"] = action
    if target_id:
        q["targetId"] = target_id
    total = await db.admin_audit_log.count_documents(q)
    rows = await db.admin_audit_log.find(q, {"_id": 0}).sort(
        "createdAt", -1).skip((page - 1) * limit).limit(limit).to_list(limit)
    return rows, total
