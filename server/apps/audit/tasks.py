"""Periodic audit jobs: checkpoints and verification (SPEC §8.7, §9.5, §9.6).

Each job takes a Postgres advisory lock so only one instance runs at a time.
``write_audit_checkpoints`` runs hourly; ``verify_audit_chains`` runs daily
incremental and weekly full-with-reconcile.
"""

from __future__ import annotations

from celery import shared_task
from django.db import connection
from django.utils import timezone

from . import service
from .models import VerificationMode

_CHECKPOINT_LOCK = 0x4144_0002
_VERIFY_LOCK = 0x4144_0003


def _try_lock(key: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        return bool(cur.fetchone()[0])


def _unlock(key: int) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [key])


@shared_task(queue="jobs", ignore_result=True)
def write_audit_checkpoints() -> int | None:  # pragma: no cover - celery wrapper
    if not _try_lock(_CHECKPOINT_LOCK):
        return None
    try:
        return service.write_all_checkpoints(timezone.now())
    finally:
        _unlock(_CHECKPOINT_LOCK)


@shared_task(queue="jobs", ignore_result=True)
def verify_audit_chains(mode: str = VerificationMode.INCREMENTAL) -> int | None:  # pragma: no cover
    if not _try_lock(_VERIFY_LOCK):
        return None
    try:
        return service.verify_all_chains(mode, timezone.now())
    finally:
        _unlock(_VERIFY_LOCK)
