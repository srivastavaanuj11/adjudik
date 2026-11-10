"""Retention, purge, legal hold and salt destruction (SPEC §9.7).

Two field groups age out independently: *input* (raw input, minimised input,
trace, metadata, external/idempotency keys, salt, injection flags, and the model
call text) and *explanation* (model rationale, review reason text and notes).
Purging nulls the group's columns and records ``input_purged_at`` /
``explanation_purged_at``; outcomes, scores, enums, hashes and audit events are
never touched, so the chain still verifies and metrics still compute. Destroying
the salt breaks the link between a stored hash and any guessed input.
"""

from __future__ import annotations

import datetime as _dt

from django.conf import settings
from django.db import transaction

from apps.common import audit
from apps.common.audit import Event
from apps.common.hashing import content_hash

from .models import Evaluation, EvaluationStatus, ModelCall

PURGE_BATCH = 1000

INPUT_FIELDS = [
    "input",
    "minimized_input",
    "rule_trace",
    "metadata",
    "external_id",
    "idempotency_key",
    "salt",
    "injection_flags",
]
EXPLANATION_FIELDS = ["model_rationale"]


def _purge_input(evaluation: Evaluation, now: _dt.datetime) -> None:
    evaluation.input = None
    evaluation.minimized_input = None
    evaluation.rule_trace = None
    evaluation.metadata = None
    evaluation.external_id = None
    evaluation.idempotency_key = None
    evaluation.salt = None  # salt destruction (SPEC §9.7)
    evaluation.injection_flags = None
    evaluation.input_purged_at = now
    ModelCall.objects.filter(evaluation=evaluation).update(
        system=None, user=None, response_text=None, parsed_output=None
    )


def _purge_explanation(evaluation: Evaluation, now: _dt.datetime) -> None:
    evaluation.model_rationale = None
    evaluation.explanation_purged_at = now
    from apps.reviews.models import ReviewTask

    ReviewTask.objects.filter(evaluation=evaluation).update(reason_text=None, notes=None)


def purge_evaluation(evaluation: Evaluation, scope: str, now: _dt.datetime) -> None:
    """Null one evaluation's purgeable columns for ``scope`` (``input`` or ``all``)."""
    _purge_input(evaluation, now)
    if scope == "all":
        _purge_explanation(evaluation, now)
    evaluation.updated_at = now
    evaluation.save()


def _ticket_ref_hash(evaluation: Evaluation, ticket_ref: str | None) -> str | None:
    if not ticket_ref:
        return None
    # Hash with the evaluation's salt while it still exists; recompute is never
    # needed, so a null salt (already purged) yields a stable keyless hash.
    salt = bytes(evaluation.salt) if evaluation.salt is not None else b""
    return content_hash(salt, ticket_ref)


def purge_one(
    evaluation: Evaluation,
    *,
    scope: str,
    reason: str,
    ticket_ref: str | None,
    actor: tuple[str, str | None],
    now: _dt.datetime,
) -> None:
    """Purge a single evaluation now and write ``evaluation.data_purged`` (SPEC §9.7)."""
    ref_hash = _ticket_ref_hash(evaluation, ticket_ref)
    with transaction.atomic():
        purge_evaluation(evaluation, scope, now)
        audit.append_many(
            evaluation.workspace_id,
            [_purged_event([str(evaluation.id)], scope, reason, ref_hash, actor, now)],
        )


def _purged_event(eval_ids, scope, reason, ref_hash, actor, now) -> Event:
    return Event(
        "evaluation.data_purged",
        now,
        actor_type=actor[0],
        actor_id=actor[1],
        payload={
            "evaluation_ids": eval_ids[:PURGE_BATCH],
            "scope": scope,
            "reason": reason,
            "ticket_ref_hash": ref_hash,
        },
    )


# --- daily retention job (SPEC §9.7) ---


def purge_expired_data(*, now: _dt.datetime) -> dict[str, int]:
    """Null fields aged past retention, in batches of 1,000 (SPEC §9.7).

    An evaluation is eligible when ``coalesce(decided_at, cancelled_at)`` is older
    than the retention window, it is ``decided`` or ``cancelled``, and it carries
    no legal hold. Input and explanation age out separately. Batches never cross a
    workspace, so each ``evaluation.data_purged`` event lands on its own chain.
    """
    from django.db.models import Q
    from django.db.models.functions import Coalesce

    input_cutoff = now - _dt.timedelta(days=settings.ADJUDIK_INPUT_RETENTION_DAYS)
    explanation_cutoff = now - _dt.timedelta(days=settings.ADJUDIK_EXPLANATION_RETENTION_DAYS)
    terminal = [EvaluationStatus.DECIDED, EvaluationStatus.CANCELLED]

    base = Evaluation.objects.filter(
        status__in=terminal, legal_hold=False, decision__legal_hold=False
    ).annotate(terminal_at=Coalesce("decided_at", "cancelled_at"))

    # Explanation purge implies input purge (retention requires explanation >=
    # input), so collect those first and exclude them from the input-only pass.
    explanation = list(
        base.filter(terminal_at__lt=explanation_cutoff, explanation_purged_at__isnull=True)
        .values_list("workspace_id", "id")
        .order_by("workspace_id")[:PURGE_BATCH]
    )
    explanation_ids = {row[1] for row in explanation}
    input_only = list(
        base.filter(terminal_at__lt=input_cutoff, input_purged_at__isnull=True)
        .filter(~Q(id__in=explanation_ids))
        .values_list("workspace_id", "id")
        .order_by("workspace_id")[:PURGE_BATCH]
    )

    explanation_purged = _purge_grouped(explanation, "all", now)
    input_purged = _purge_grouped(input_only, "input", now)
    return {"input_purged": input_purged, "explanation_purged": explanation_purged}


def _purge_grouped(rows, scope: str, now: _dt.datetime) -> int:
    """Purge ``(workspace_id, id)`` rows, one transaction and one event per workspace."""
    by_ws: dict = {}
    for workspace_id, eval_id in rows:
        by_ws.setdefault(workspace_id, []).append(eval_id)
    total = 0
    for workspace_id, ids in by_ws.items():
        with transaction.atomic():
            for evaluation in Evaluation.objects.select_for_update(no_key=True).filter(id__in=ids):
                purge_evaluation(evaluation, scope, now)
            audit.append_many(
                workspace_id,
                [
                    _purged_event(
                        [str(i) for i in ids], scope, "retention", None, ("system", None), now
                    )
                ],
            )
        total += len(ids)
    return total
