"""Workspace, membership and API-key services (SPEC §15.2, §16.6).

Creating a workspace also creates its settings (with a fresh encrypted HMAC key)
and its audit chain head, so the chain is appendable from seq 1.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.contrib.auth.models import User
from django.db import transaction

from adjudik_core.canonical import ZERO_HASH
from apps.audit.models import AuditChainHead
from apps.common import crypto
from apps.common.hashing import new_workspace_key

from .models import ApiKey, Membership, UserPasswordState, Workspace, WorkspaceSettings

SYSTEM_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")

# API-key format: adk_<8-char prefix>_<43-char url-safe secret> (SPEC §15.2).
KEY_PREFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
SECRET_BYTES = 32  # token_urlsafe(32) -> 43 chars
MAX_KEY_LIFETIME_DAYS = 365


@dataclass
class IssuedKey:
    api_key: ApiKey
    full_key: str  # returned once


def _random_prefix() -> str:
    return "".join(secrets.choice(KEY_PREFIX_ALPHABET) for _ in range(8))


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("ascii")).hexdigest()


@transaction.atomic
def create_workspace(slug: str, name: str, *, now: _dt.datetime) -> Workspace:
    """Create a workspace with settings, HMAC key and chain head (idempotent)."""
    ws, created = Workspace.objects.get_or_create(
        slug=slug, defaults={"name": name, "created_at": now}
    )
    if created:
        WorkspaceSettings.objects.create(
            workspace=ws,
            hmac_key_encrypted=crypto.encrypt(new_workspace_key()),
            updated_at=now,
        )
        AuditChainHead.objects.create(workspace_id=ws.id, seq=0, hash=ZERO_HASH)
    # Ensure the system chain head exists for account-level auth.* events.
    AuditChainHead.objects.get_or_create(
        workspace_id=SYSTEM_WORKSPACE_ID, defaults={"seq": 0, "hash": ZERO_HASH}
    )
    return ws


def workspace_hmac_key(ws: Workspace) -> bytes:
    return crypto.decrypt(ws.settings.hmac_key_encrypted)


@transaction.atomic
def create_user_with_temp_password(username: str, *, now: _dt.datetime) -> tuple[User, str]:
    """Create a global user with a random temp password that expires in 24 h.

    The user must change it at first login (``password_change_required``).
    """
    model = get_user_model()
    temp = secrets.token_urlsafe(16)
    user = model.objects.create_user(username=username, password=temp)
    UserPasswordState.objects.update_or_create(
        user=user,
        defaults={"must_change": True, "temp_expires_at": now + _dt.timedelta(hours=24)},
    )
    return user, temp


@transaction.atomic
def add_member(ws: Workspace, user: User, role: str, *, now: _dt.datetime) -> Membership:
    return Membership.objects.create(workspace=ws, user=user, role=role, created_at=now)


@transaction.atomic
def issue_api_key(
    ws: Workspace,
    *,
    name: str,
    scopes: list[str],
    decision_ids: list[str] | None,
    now: _dt.datetime,
    created_by: str | None,
    lifetime_days: int = MAX_KEY_LIFETIME_DAYS,
    expires_at: _dt.datetime | None = None,
) -> IssuedKey:
    lifetime_days = min(lifetime_days, MAX_KEY_LIFETIME_DAYS)
    if expires_at is None:
        expires_at = now + _dt.timedelta(days=lifetime_days)
    for _ in range(10):
        prefix = _random_prefix()
        if not ApiKey.objects.filter(prefix=prefix).exists():
            break
    else:  # pragma: no cover - astronomically unlikely
        raise RuntimeError("could not allocate a unique API-key prefix")
    secret = secrets.token_urlsafe(SECRET_BYTES)
    api_key = ApiKey.objects.create(
        workspace=ws,
        name=name,
        prefix=prefix,
        key_hash=hash_secret(secret),
        scopes=scopes,
        decision_ids=decision_ids,
        expires_at=expires_at,
        created_by=created_by,
        created_at=now,
    )
    return IssuedKey(api_key=api_key, full_key=f"adk_{prefix}_{secret}")


@transaction.atomic
def rotate_api_key(api_key: ApiKey, *, now: _dt.datetime) -> IssuedKey:
    """Issue a new secret; the old one stays valid for 24 h (SPEC §15.2).

    The rotation keeps the same prefix and row; the previous secret hash is
    retained on the row so the old secret authenticates during the grace window.
    """
    secret = secrets.token_urlsafe(SECRET_BYTES)
    api_key.previous_key_hash = api_key.key_hash
    api_key.previous_key_expires_at = now + _dt.timedelta(hours=24)
    api_key.key_hash = hash_secret(secret)
    api_key.save(update_fields=["key_hash", "previous_key_hash", "previous_key_expires_at"])
    return IssuedKey(api_key=api_key, full_key=f"adk_{api_key.prefix}_{secret}")
