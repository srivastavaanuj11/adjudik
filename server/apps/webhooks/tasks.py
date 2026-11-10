"""Webhook periodic jobs (SPEC §8.7, §11.6).

``dispatch_webhooks`` runs every 5 s (and is nudged on commit); it claims and
sends due deliveries. ``prune_webhook_deliveries`` runs daily. Both take a
Postgres advisory lock and skip the run when another instance holds it.
"""

from __future__ import annotations

from celery import shared_task
from django.db import connection
from django.utils import timezone

from . import service

_DISPATCH_LOCK = 0x4144_0004
_PRUNE_LOCK = 0x4144_0005


def _try_lock(key: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        return bool(cur.fetchone()[0])


def _unlock(key: int) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [key])


@shared_task(ignore_result=True)
def dispatch_webhooks_task() -> dict[str, int] | None:  # pragma: no cover - celery wrapper
    if not _try_lock(_DISPATCH_LOCK):
        return None
    try:
        return service.dispatch_due(now=timezone.now())
    finally:
        _unlock(_DISPATCH_LOCK)


@shared_task(ignore_result=True)
def prune_webhook_deliveries_task() -> int | None:  # pragma: no cover - celery wrapper
    if not _try_lock(_PRUNE_LOCK):
        return None
    try:
        return service.prune_deliveries(now=timezone.now())
    finally:
        _unlock(_PRUNE_LOCK)
