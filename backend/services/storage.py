"""Cloudflare R2 object storage via boto3 (S3-compatible).

Environment variables:
- R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY
- R2_BUCKET_NAME (default `seojalwa-assets`)
- R2_PUBLIC_URL (e.g. https://pub-xxxxx.r2.dev) — used to construct public URLs

All functions fail soft: if credentials are not set, they log a warning and
return a deterministic placeholder so calling code never crashes.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger("jalwa.storage")


def _r2_endpoint(account: str | None = None) -> Optional[str]:
    account = account or os.environ.get("R2_ACCOUNT_ID")
    if not account:
        return None
    return f"https://{account}.r2.cloudflarestorage.com"


async def _r2_config() -> dict:
    """DB-first config lookup."""
    from services.config import config_service
    fields = await config_service.get_fields("cloudflare_r2")
    return {
        "account_id": fields.get("account_id") or os.environ.get("R2_ACCOUNT_ID", ""),
        "access_key_id": fields.get("access_key_id") or os.environ.get("R2_ACCESS_KEY_ID", ""),
        "secret_access_key": fields.get("secret_access_key") or os.environ.get("R2_SECRET_ACCESS_KEY", ""),
        "bucket_name": fields.get("bucket_name") or os.environ.get("R2_BUCKET_NAME", "seojalwa-assets"),
        "public_url": fields.get("public_url") or os.environ.get("R2_PUBLIC_URL", ""),
    }


async def _client_async():
    """Lazily build the boto3 S3 client from DB config. Returns (client, cfg)."""
    cfg = await _r2_config()
    if not cfg["account_id"]:
        return None, cfg
    try:
        import boto3
        from botocore.config import Config
        return boto3.client(
            "s3",
            endpoint_url=f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
            aws_access_key_id=cfg["access_key_id"],
            aws_secret_access_key=cfg["secret_access_key"],
            config=Config(signature_version="s3v4"),
            region_name="auto",
        ), cfg
    except Exception as e:
        logger.warning("R2 client init failed: %s", e)
        return None, cfg


def _client():
    """Synchronous fallback client (env-only) — kept for any legacy callers."""
    if not _r2_endpoint():
        return None
    try:
        import boto3
        from botocore.config import Config
        return boto3.client(
            "s3",
            endpoint_url=_r2_endpoint(),
            aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    except Exception as e:
        logger.warning("R2 client init failed: %s", e)
        return None


def _bucket() -> str:
    return os.environ.get("R2_BUCKET_NAME", "seojalwa-assets")


def _public_url(key: str, base: str | None = None) -> str:
    base = (base or os.environ.get("R2_PUBLIC_URL", "")).rstrip("/")
    if base:
        return f"{base}/{key}"
    return f"{_r2_endpoint() or 'https://mock-r2.test'}/{_bucket()}/{key}"


# -------------------------------------------------------------------- upload
async def upload_file(file_bytes: bytes, key: str,
                      content_type: str = "application/octet-stream") -> str:
    """Upload bytes to R2 and return the public URL."""
    client, cfg = await _client_async()
    if not client:
        logger.info("[R2 skipped — credentials missing] key=%s bytes=%d",
                    key, len(file_bytes))
        return _public_url(key, cfg.get("public_url"))
    try:
        client.put_object(
            Bucket=cfg["bucket_name"], Key=key, Body=file_bytes,
            ContentType=content_type,
            CacheControl="public, max-age=31536000",
        )
        url = _public_url(key, cfg.get("public_url"))
        logger.info("[R2 uploaded] key=%s bytes=%d", key, len(file_bytes))
        return url
    except Exception as e:
        logger.exception("R2 upload failed: %s", e)
        return _public_url(key, cfg.get("public_url"))


async def delete_file(key: str) -> bool:
    client, cfg = await _client_async()
    if not client:
        return False
    try:
        client.delete_object(Bucket=cfg["bucket_name"], Key=key)
        return True
    except Exception as e:
        logger.warning("R2 delete failed: %s", e)
        return False


async def get_signed_url(key: str, expires: int = 3600) -> Optional[str]:
    client, cfg = await _client_async()
    if not client:
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": cfg["bucket_name"], "Key": key},
            ExpiresIn=expires)
    except Exception as e:
        logger.warning("R2 signed URL failed: %s", e)
        return None


# ------------------------------------------------------------------ download
async def download_to_r2(source_url: str, key: str,
                         content_type: str = "image/jpeg") -> str:
    """Fetch a remote URL (e.g. OpenAI image) and re-upload to R2."""
    try:
        import httpx
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.get(source_url)
            r.raise_for_status()
            return await upload_file(r.content, key, content_type)
    except Exception as e:
        logger.warning("download_to_r2 failed for %s: %s", source_url, e)
        return source_url  # fall back to the original URL


async def test_r2() -> dict:
    """Used by admin api-keys/test — lists objects (max 1) in the bucket."""
    client, cfg = await _client_async()
    if not client:
        return {"success": False, "message": "R2 credentials not configured"}
    try:
        client.list_objects_v2(Bucket=cfg["bucket_name"], MaxKeys=1)
        return {"success": True,
                "message": f"R2 bucket '{cfg['bucket_name']}' reachable"}
    except Exception as e:
        return {"success": False, "message": str(e)}
