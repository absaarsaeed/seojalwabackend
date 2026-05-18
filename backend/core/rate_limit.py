"""Minimal in-memory rate limiter for IP-based / endpoint throttling."""
import time
from collections import defaultdict
from typing import Tuple

from fastapi import Request
from core.response import APIError


_buckets: dict[str, list[float]] = defaultdict(list)


def _hit(key: str, max_calls: int, window_seconds: int) -> Tuple[bool, int]:
    now = time.time()
    window_start = now - window_seconds
    bucket = [t for t in _buckets[key] if t > window_start]
    _buckets[key] = bucket
    if len(bucket) >= max_calls:
        retry = int(bucket[0] + window_seconds - now) + 1
        return False, retry
    bucket.append(now)
    return True, 0


def rate_limit(scope: str, max_calls: int, window_seconds: int):
    """Returns a FastAPI dependency that enforces a per-IP rate limit."""
    async def dependency(request: Request):
        ip = request.client.host if request.client else "anon"
        key = f"{scope}:{ip}"
        ok, retry = _hit(key, max_calls, window_seconds)
        if not ok:
            raise APIError(
                f"Rate limit exceeded. Retry in {retry}s.",
                code="RATE_LIMITED",
                status_code=429,
            )
    return dependency


def admin_lockout_check(ip: str) -> Tuple[bool, int]:
    """5 attempts then 30 min lockout."""
    return _hit(f"admin_login:{ip}", 5, 1800)
