"""Admin — upload and manage the SEO Jalwa WordPress plugin ZIP."""
from fastapi import APIRouter, Depends, File, UploadFile

from core.database import get_db
from core.dependencies import get_admin_session
from core.response import APIError, ok
from core.security import utcnow_iso
from services import storage

router = APIRouter(prefix="/admin/plugin", tags=["admin-plugin"],
                   dependencies=[Depends(get_admin_session)])

_PLUGIN_R2_KEY = "plugin/seojalwa-plugin.zip"


async def _set_setting(key: str, value: str) -> None:
    await get_db().settings.update_one(
        {"key": key},
        {"$set": {"key": key, "value": value, "updatedAt": utcnow_iso()}},
        upsert=True)


async def _get_setting(key: str, default: str = "") -> str:
    doc = await get_db().settings.find_one({"key": key}, {"_id": 0})
    return (doc or {}).get("value", default)


@router.get("/info")
async def plugin_info():
    return ok({
        "version": await _get_setting("plugin_version", "1.0.0"),
        "download_url": await _get_setting("plugin_download_url", ""),
        "changelog": await _get_setting("plugin_changelog", ""),
    })


@router.post("/upload")
async def upload_plugin(file: UploadFile = File(...)):
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise APIError("Only .zip files are accepted",
                       "INVALID_FILE_TYPE", 400)

    contents = await file.read()
    if not contents:
        raise APIError("Uploaded file is empty",
                       "EMPTY_FILE", 400)

    download_url = await storage.upload_file(
        contents, _PLUGIN_R2_KEY,
        content_type="application/zip")

    await _set_setting("plugin_download_url", download_url)

    return ok({
        "uploaded": True,
        "download_url": download_url,
        "size_bytes": len(contents),
    }, "Plugin uploaded successfully")
