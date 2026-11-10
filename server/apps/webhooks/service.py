"""Webhook outbox and payloads (SPEC §11.6).

Every state change inserts ``webhook_delivery`` rows — one per active, subscribed
endpoint — in the *same* transaction as the change itself (the outbox pattern),
so a delivery is never lost and never sent for a change that rolled back. Payloads
are thin: ids, enums, numbers and timestamps only, never inputs, rationale or
metadata (SPEC §11.6, §15.5). Signing and secret rotation live in
:mod:`apps.webhooks.sender`.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any

from django.conf import settings
from django.db import models, transaction

from apps.common.clock import iso_micros

from .models import DeliveryStatus, WebhookDelivery, WebhookEndpoint, WebhookEventType


def _endpoints_for(workspace_id, decision_id, event_type: str):
    """Active endpoints in the workspace subscribed to ``event_type``.

    An endpoint with a null ``decision_id`` covers every decision; one bound to a
    decision covers only that decision (SPEC §10.1).
    """
    return WebhookEndpoint.objects.filter(
        workspace_id=workspace_id,
        active=True,
        event_types__contains=[event_type],
    ).filter(models.Q(decision_id__isnull=True) | models.Q(decision_id=decision_id))


def _thin_payload(event_id: str, event_type: str, now: _dt.datetime, data: dict[str, Any]) -> dict:
    return {
        "id": event_id,
        "type": event_type,
        "created_at": iso_micros(now),
        "data": data,
    }


def evaluation_data(evaluation) -> dict[str, Any]:
    """The common ``data`` block for an evaluation webhook (SPEC §11.6).

    ``external_id`` is null once purged, which falls out of the stored value.
    """
    return {
        "evaluation_id": str(evaluation.id),
        "decision": evaluation.decision.key,
        "version": evaluation.decision_version.version,
        "external_id": evaluation.external_id,
        "status": evaluation.status,
        "outcome": evaluation.outcome,
        "decided_by": evaluation.decided_by,
        "route_reason": evaluation.route_reason,
        "state_version": evaluation.state_version,
    }


def enqueue_event(
    *,
    workspace_id,
    decision_id,
    event_type: str,
    now: _dt.datetime,
    data: dict[str, Any],
    evaluation_id=None,
) -> str | None:
    """Insert outbox rows for one logical event; return its shared event id.

    Must run inside the state change's transaction. One row per matching endpoint,
    all sharing a single ``event_id`` so receivers can deduplicate (SPEC §11.6).
    Returns ``None`` when no endpoint matches.
    """
    endpoints = list(_endpoints_for(workspace_id, decision_id, event_type))
    if not endpoints:
        return None

    event_id = str(uuid.uuid4())
    payload = _thin_payload(event_id, event_type, now, data)
    rows = [
        WebhookDelivery(
            workspace_id=workspace_id,
            endpoint=endpoint,
            event_id=event_id,
            event_type=event_type,
            evaluation_id=evaluation_id,
            payload=payload,
            attempt=0,
            status=DeliveryStatus.PENDING,
            next_attempt_at=now,
            created_at=now,
        )
        for endpoint in endpoints
    ]
    WebhookDelivery.objects.bulk_create(rows)

    if getattr(settings, "ADJUDIK_WEBHOOK_NUDGE_ON_COMMIT", True):
        # Latency optimisation: wake the dispatcher on commit (SPEC §8.7). The
        # 5 s periodic job delivers regardless, so a lost nudge is harmless.
        def _nudge() -> None:  # pragma: no cover - exercised in dev, not unit tests
            from . import tasks

            tasks.dispatch_webhooks_task.delay()

        transaction.on_commit(_nudge)
    return event_id


# --- high-level emitters (called inside the state-change transaction) ---


def emit_evaluation_event(evaluation, event_type: str, now: _dt.datetime, *, extra=None) -> None:
    data = evaluation_data(evaluation)
    if extra:
        data.update(extra)
    enqueue_event(
        workspace_id=evaluation.workspace_id,
        decision_id=evaluation.decision_id,
        event_type=event_type,
        now=now,
        data=data,
        evaluation_id=evaluation.id,
    )


def emit_decided(evaluation, now: _dt.datetime) -> None:
    emit_evaluation_event(evaluation, WebhookEventType.EVALUATION_DECIDED, now)


def emit_pending_review(evaluation, now: _dt.datetime) -> None:
    emit_evaluation_event(evaluation, WebhookEventType.EVALUATION_PENDING_REVIEW, now)


def emit_cancelled(evaluation, now: _dt.datetime) -> None:
    emit_evaluation_event(evaluation, WebhookEventType.EVALUATION_CANCELLED, now)


def emit_audit_disagreement(evaluation, review_task, now: _dt.datetime) -> None:
    emit_evaluation_event(
        evaluation,
        WebhookEventType.EVALUATION_AUDIT_DISAGREEMENT,
        now,
        extra={
            "audit": {
                "review_task_id": str(review_task.id),
                "reviewer_outcome": review_task.final_outcome,
                "completed_at": iso_micros(review_task.completed_at),
            }
        },
    )


def dispatch_due(*, now: _dt.datetime, limit: int = 50, deliver=None) -> dict[str, int]:
    """Claim due deliveries, send them, and reschedule or finalise (SPEC §11.6).

    Claiming locks rows ``FOR UPDATE SKIP LOCKED`` and stamps ``locked_until`` in
    its own transaction; sending happens outside that lock so a slow endpoint does
    not hold rows. ``deliver`` is injectable for tests.
    """
    from . import sender as _sender

    deliver = deliver or _sender.deliver
    claimed = _claim_due(now, limit)
    sent = failed = dead = 0
    for delivery in claimed:
        outcome = deliver(delivery, now=now)
        with transaction.atomic():
            row = WebhookDelivery.objects.select_for_update().get(pk=delivery.pk)
            row.attempt += 1
            row.last_status_code = outcome.status_code
            row.locked_until = None
            if outcome.ok:
                row.status = DeliveryStatus.SUCCEEDED
                row.delivered_at = now
                row.last_error_class = None
                row.next_attempt_at = None
                sent += 1
            else:
                row.last_error_class = outcome.error_class
                delay = _sender.next_delay_seconds(row.attempt)
                if delay is None:
                    row.status = DeliveryStatus.DEAD
                    row.next_attempt_at = None
                    dead += 1
                else:
                    row.status = DeliveryStatus.FAILED
                    row.next_attempt_at = now + _dt.timedelta(seconds=delay)
                    failed += 1
            row.save()
    return {"sent": sent, "failed": failed, "dead": dead}


def _claim_due(now: _dt.datetime, limit: int) -> list[WebhookDelivery]:
    with transaction.atomic():
        rows = list(
            WebhookDelivery.objects.select_for_update(skip_locked=True)
            .filter(
                status__in=[DeliveryStatus.PENDING, DeliveryStatus.FAILED],
                next_attempt_at__lte=now,
            )
            .filter(models.Q(locked_until__isnull=True) | models.Q(locked_until__lte=now))
            .order_by("next_attempt_at")[:limit]
        )
        ids = [r.pk for r in rows]
        if ids:
            WebhookDelivery.objects.filter(pk__in=ids).update(
                locked_until=now + _dt.timedelta(seconds=60)
            )
    return (
        list(WebhookDelivery.objects.select_related("endpoint").filter(pk__in=[r.pk for r in rows]))
        if rows
        else []
    )


def retry_delivery(delivery: WebhookDelivery, *, now: _dt.datetime) -> None:
    """Reset a failed or dead delivery to pending for an immediate attempt (SPEC §11.2)."""
    delivery.status = DeliveryStatus.PENDING
    delivery.next_attempt_at = now
    delivery.locked_until = None
    delivery.save(update_fields=["status", "next_attempt_at", "locked_until"])


def prune_deliveries(*, now: _dt.datetime) -> int:
    """Delete succeeded and dead deliveries older than 30 days (SPEC §8.7)."""
    cutoff = now - _dt.timedelta(days=30)
    deleted, _ = WebhookDelivery.objects.filter(
        status__in=[DeliveryStatus.SUCCEEDED, DeliveryStatus.DEAD], created_at__lt=cutoff
    ).delete()
    return deleted


def emit_automation_suspended(
    *, workspace_id, decision, version: int, outcome: str, policy_id, now: _dt.datetime
) -> None:
    enqueue_event(
        workspace_id=workspace_id,
        decision_id=decision.id,
        event_type=WebhookEventType.AUTOMATION_SUSPENDED,
        now=now,
        data={
            "decision": decision.key,
            "version": version,
            "outcome": outcome,
            "policy_id": str(policy_id),
        },
    )
