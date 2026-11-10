"""Review lease-expiry job (SPEC §8.7).

``release_expired_leases`` runs every minute: any claimed task whose lease has
passed returns to ``open`` with a ``review.released`` event (``by: lease_expiry``).
The job takes a Postgres advisory lock and skips when another instance holds it.
"""

from __future__ import annotations

import datetime as _dt

from celery import shared_task
from django.db import connection, transaction
from django.utils import timezone

from apps.common import audit
from apps.common.audit import Event

from .models import ReviewStatus, ReviewTask

_LEASE_LOCK = 0x4144_0006


def release_expired_leases(*, now: _dt.datetime | None = None) -> int:
    """Return expired claimed tasks to ``open``; one event per workspace (SPEC §8.7)."""
    now = now or timezone.now()
    expired = list(
        ReviewTask.objects.filter(
            status=ReviewStatus.CLAIMED, lease_expires_at__lte=now
        ).values_list("workspace_id", "id", "evaluation_id")
    )
    by_ws: dict = {}
    for workspace_id, task_id, evaluation_id in expired:
        by_ws.setdefault(workspace_id, []).append((task_id, evaluation_id))

    released = 0
    for workspace_id, rows in by_ws.items():
        with transaction.atomic():
            events = []
            for task_id, evaluation_id in rows:
                updated = ReviewTask.objects.filter(
                    id=task_id, status=ReviewStatus.CLAIMED, lease_expires_at__lte=now
                ).update(
                    status=ReviewStatus.OPEN,
                    claimed_by=None,
                    claimed_at=None,
                    claim_via=None,
                    lease_expires_at=None,
                )
                if not updated:
                    continue
                events.append(
                    Event(
                        "review.released",
                        now,
                        actor_type="system",
                        evaluation_id=evaluation_id,
                        subject_type="review_task",
                        subject_id=str(task_id),
                        payload={"review_task_id": str(task_id), "by": "lease_expiry"},
                    )
                )
            if events:
                audit.append_many(workspace_id, events)
            released += len(events)
    return released


def _try_lock(key: int) -> bool:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [key])
        return bool(cur.fetchone()[0])


def _unlock(key: int) -> None:
    with connection.cursor() as cur:
        cur.execute("SELECT pg_advisory_unlock(%s)", [key])


@shared_task(ignore_result=True)
def release_expired_leases_task() -> int | None:  # pragma: no cover - celery wrapper
    if not _try_lock(_LEASE_LOCK):
        return None
    try:
        return release_expired_leases()
    finally:
        _unlock(_LEASE_LOCK)
