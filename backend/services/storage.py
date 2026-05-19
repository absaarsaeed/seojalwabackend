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


def _r2_endpoint() -> Optional[str]:
    account = os.environ.get("R2_ACCOUNT_ID")
    if not account:
        return None
    return f"https://{account}.r2.cloudflarestorage.com"


def _client():
    """Lazily build the boto3 S3 client. Returns None if not configured."""
    if not _r2_endpoint():
        return None
    try:
        import boto3  # local import — keeps cold-import fast
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


def _public_url(key: str) -> str:
    base = os.environ.get("R2_PUBLIC_URL", "").rstrip("/")
    if base:
        return f"{base}/{key}"
    # Fallback: still return a deterministic path so the UI keeps working
    return f"{_r2_endpoint() or 'https://mock-r2.test'}/{_bucket()}/{key}"


# -------------------------------------------------------------------- upload
async def upload_file(file_bytes: bytes, key: str,
                      content_type: str = "application/octet-stream") -> str:
    """Upload bytes to R2 and return the public URL."""
    client = _client()
    if not client:
        logger.info("[R2 skipped — credentials missing] key=%s bytes=%d",
                    key, len(file_bytes))
        return _public_url(key)
    try:
        client.put_object(
            Bucket=_bucket(),
            Key=key,
            Body=file_bytes,
            ContentType=content_type,
            CacheControl="public, max-age=31536000",
        )
        url = _public_url(key)
        logger.info("[R2 uploaded] key=%s bytes=%d", key, len(file_bytes))
        return url
    except Exception as e:
        logger.exception("R2 upload failed: %s", e)
        return _public_url(key)


# -------------------------------------------------------------------- delete
async def delete_file(key: str) -> bool:
    client = _client()
    if not client:
        return False
    try:
        client.delete_object(Bucket=_bucket(), Key=key)
        return True
    except Exception as e:
        logger.warning("R2 delete failed: %s", e)
        return False


# -------------------------------------------------------------------- signed
async def get_signed_url(key: str, expires: int = 3600) -> Optional[str]:
    client = _client()
    if not client:
        return None
    try:
        return client.generate_presigned_url(
            "get_object", Params={"Bucket": _bucket(), "Key": key},
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
    client = _client()
    if not client:
        return {"success": False, "message": "R2 credentials not configured"}
    try:
        client.list_objects_v2(Bucket=_bucket(), MaxKeys=1)
        return {"success": True, "message": f"R2 bucket '{_bucket()}' reachable"}
    except Exception as e:
        return {"success": False, "message": str(e)}
