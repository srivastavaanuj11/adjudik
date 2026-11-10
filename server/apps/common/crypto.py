"""Symmetric encryption for stored secrets (SPEC §15.3).

Webhook secrets and per-workspace HMAC keys are encrypted at rest with
``MultiFernet`` over ``ADJUDIK_ENCRYPTION_KEYS`` (comma-separated, newest
first), so keys can be rotated: the newest key encrypts, any key may decrypt.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@lru_cache(maxsize=1)
def _multifernet() -> MultiFernet:
    raw = (settings.ADJUDIK_ENCRYPTION_KEYS or "").strip()
    if not raw:
        raise ImproperlyConfigured("ADJUDIK_ENCRYPTION_KEYS is not set (SPEC §15.3).")
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    try:
        fernets = [Fernet(k.encode("ascii")) for k in keys]
    except (ValueError, TypeError) as exc:
        raise ImproperlyConfigured(f"ADJUDIK_ENCRYPTION_KEYS is malformed: {exc}") from exc
    return MultiFernet(fernets)


def encrypt(value: bytes | str) -> str:
    """Encrypt ``value`` with the newest key; return a base64 token (str)."""
    data = value.encode("utf-8") if isinstance(value, str) else value
    return _multifernet().encrypt(data).decode("ascii")


def decrypt(token: str) -> bytes:
    """Decrypt a token produced by :func:`encrypt` with any configured key."""
    return _multifernet().decrypt(token.encode("ascii"))


def decrypt_str(token: str) -> str:
    return decrypt(token).decode("utf-8")


def reset_cache() -> None:
    """Drop the cached MultiFernet (tests that change the setting)."""
    _multifernet.cache_clear()
