"""AES-style symmetric encryption using Fernet for tokens / API keys at rest."""
import base64
import hashlib
import os
from cryptography.fernet import Fernet


def _get_fernet() -> Fernet:
    raw = os.environ.get("ENCRYPTION_KEY", "jalwa-default-key-please-change")
    # Derive a 32-byte key deterministically
    digest = hashlib.sha256(raw.encode()).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(plain: str | None) -> str | None:
    if plain is None:
        return None
    return _get_fernet().encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if token is None:
        return None
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except Exception:
        return None


def mask(value: str | None) -> str:
    """Mask a key showing only last 4 chars."""
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]
