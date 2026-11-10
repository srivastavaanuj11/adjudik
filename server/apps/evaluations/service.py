"""Evaluate flow: submit, route and persist (SPEC §11.3, §8.2).

The server drives the core step functions and builds its own audit events with
salted content hashes and real row ids (SPEC §4.5, ADR 0004). ``submit`` runs
steps 1-6 of §11.3; the worker (``tasks.run_model_step``) reuses
:func:`apply_routing` once the model has run.
"""

from __future__ import annotations

import datetime as _dt
import random
from dataclasses import dataclass
from typing import Any

from django.db import IntegrityError, transaction

from adjudik_core import (
    CompiledDefinition,
    EvaluationContext,
    InputValidationError,
    load_definition,
)
from adjudik_core.engine import Decided, Gate
from apps.accounts import service as accounts_service
from apps.common import audit
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.engine import build_engine
from apps.common.errors import conflict, unprocessable
from apps.common.hashing import content_hash, lookup_hmac, new_salt, normalise_request

from .models import DecidedBy, Evaluation, EvaluationStatus
from .waiting import publish_update

MAX_ID_LEN = 200
MAX_METADATA_BYTES = 4096


@dataclass
class SubmitResult:
    evaluation: Evaluation
    replayed: bool
    enqueue: bool  # the model worker should be enqueued on commit


# --- helpers ---


def _extract(data: Any, path: list[str]) -> Any:
    cur = data
    for part in path:
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _subject_value(defn: CompiledDefinition, data: Any) -> Any | None:
    """The composite subject value for resubmission (SPEC §8.3).

    ``subject_key`` is a list of dotted-path expressions (e.g. ``ticket.id``);
    the subject value is the list of their resolved parts.
    """
    keys = defn.definition.routing.subject_key
    if not keys:
        return None
    return [_extract(data, part.split(".")) for part in keys]


def _day_bounds(now: _dt.datetime) -> tuple[_dt.datetime, _dt.datetime]:
    start = now.astimezone(_dt.UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + _dt.timedelta(days=1)


def _budget_exceeded(workspace, now: _dt.datetime) -> bool:
    budget = workspace.settings.daily_token_budget
    if budget is None:
        return False
    from django.db.models import F, Sum

    from .models import ModelCall

    start, end = _day_bounds(now)
    agg = ModelCall.objects.filter(
        workspace=workspace, created_at__gte=start, created_at__lt=end
    ).aggregate(total=Sum(F("input_tokens") + F("output_tokens")))
    used = agg["total"] or 0
    return used >= budget


def _resubmission(defn: CompiledDefinition, workspace, decision, subject_hmac, now) -> bool:
    window = defn.definition.routing.resubmission_window_days
    if window <= 0 or subject_hmac is None:
        return False
    since = now - _dt.timedelta(days=window)
    return Evaluation.objects.filter(
        workspace=workspace,
        decision=decision,
        subject_hmac=subject_hmac,
        handed_to_model=True,
        created_at__gte=since,
    ).exists()


def _received_event(evaluation: Evaluation, now, ctx_actor) -> Event:
    return Event(
        "evaluation.received",
        now,
        actor_type=ctx_actor[0],
        actor_id=ctx_actor[1],
        evaluation_id=evaluation.id,
        subject_type="evaluation",
        subject_id=str(evaluation.id),
        payload={
            "decision_version_id": str(evaluation.decision_version_id),
            "input_hash": evaluation.input_hash,
            "minimized_input_hash": evaluation.minimized_input_hash,
            "has_external_id": evaluation.external_id is not None,
            "idempotency_key_present": evaluation.idempotency_key is not None,
        },
    )


def _rules_event(evaluation: Evaluation, rule_result, now, ctx_actor) -> Event:
    return Event(
        "rules.evaluated",
        now,
        actor_type=ctx_actor[0],
        actor_id=ctx_actor[1],
        evaluation_id=evaluation.id,
        payload={
            "type": rule_result.type,
            "matched_row_ids": rule_result.matched_row_ids,
            "row_id": rule_result.row_id,
            "outcome": rule_result.outcome,
        },
    )


# --- routing / persistence (shared by submit and the worker) ---


def apply_routing(
    *,
    evaluation: Evaluation,
    routing,
    defn: CompiledDefinition,
    salt: bytes,
    now: _dt.datetime,
    ctx_actor: tuple[str, str | None],
    rationale: str | None = None,
    automation_policy_id=None,
    calibrated_confidence: float | None = None,
) -> list[Event]:
    """Settle an evaluation from a ``Routing`` and create review tasks.

    Returns the audit events to append (the caller appends once, last).
    """
    from apps.reviews.models import ReviewKind, ReviewTask

    evaluation.state_version += 1
    review = defn.definition.review
    events: list[Event] = []

    if isinstance(routing, Decided):
        evaluation.status = EvaluationStatus.DECIDED
        evaluation.outcome = routing.outcome
        evaluation.decided_by = DecidedBy.RULE if routing.by == "rule" else DecidedBy.MODEL
        evaluation.route_reason = None
        evaluation.decided_at = now
        rationale_hash = None
        if routing.by == "model":
            evaluation.suggested_outcome = routing.outcome
            evaluation.raw_confidence = routing.raw_confidence
            evaluation.threshold = routing.threshold
            evaluation.calibrated_confidence = calibrated_confidence
            evaluation.automation_policy_id = automation_policy_id
            evaluation.model_rationale = rationale
            rationale_hash = content_hash(salt, rationale) if rationale is not None else None
        audit_sampled = routing.audit is not None
        events.append(
            _routed_event(
                evaluation, "auto", routing, now, ctx_actor, audit_sampled, rationale_hash
            )
        )
        events.append(
            Event(
                "evaluation.decided",
                now,
                actor_type=ctx_actor[0],
                actor_id=ctx_actor[1],
                evaluation_id=evaluation.id,
                payload={
                    "outcome": evaluation.outcome,
                    "decided_by": evaluation.decided_by,
                    "state_version": evaluation.state_version,
                },
            )
        )
        if routing.audit is not None:
            task = ReviewTask.objects.create(
                workspace=evaluation.workspace,
                decision=evaluation.decision,
                evaluation=evaluation,
                kind=ReviewKind.AUDIT,
                priority=1,
                reason="audit_sample",
                suggested_outcome=routing.outcome,
                suggested_confidence=routing.raw_confidence,
                blind=True,
                label_propensity=routing.audit.label_propensity,
                due_at=now + _dt.timedelta(minutes=review.sla_minutes),
                created_at=now,
            )
            events.append(_review_created_event(task, now, ctx_actor))
        _emit_webhook(evaluation, "evaluation.decided", now)
    else:  # Gate
        evaluation.status = EvaluationStatus.PENDING_REVIEW
        evaluation.route_reason = routing.reason
        rationale_hash = None
        if routing.suggestion is not None:
            evaluation.suggested_outcome = routing.suggestion
            evaluation.threshold = routing.threshold
            evaluation.calibrated_confidence = calibrated_confidence
            if rationale is not None:
                evaluation.model_rationale = rationale
                rationale_hash = content_hash(salt, rationale)
        events.append(
            _routed_event(evaluation, "review", routing, now, ctx_actor, False, rationale_hash)
        )
        label_propensity = routing.blind and _blind_label_propensity(evaluation)
        task = ReviewTask.objects.create(
            workspace=evaluation.workspace,
            decision=evaluation.decision,
            evaluation=evaluation,
            kind=ReviewKind.GATE,
            priority=0,
            reason=routing.reason,
            suggested_outcome=routing.suggestion,
            suggested_confidence=evaluation.raw_confidence if not routing.blind else None,
            blind=routing.blind,
            label_propensity=label_propensity or None,
            due_at=now + _dt.timedelta(minutes=review.sla_minutes),
            created_at=now,
        )
        events.append(_review_created_event(task, now, ctx_actor))
        _emit_webhook(evaluation, "evaluation.pending_review", now)

    evaluation.updated_at = now
    evaluation.save()
    return events


def _emit_webhook(evaluation: Evaluation, event_type: str, now: _dt.datetime) -> None:
    """Insert webhook outbox rows for an evaluation state change (SPEC §11.6)."""
    from apps.webhooks import service as webhook_service

    # ``evaluation`` already carries the related decision/version on the save path;
    # the worker path refreshes them on the locked row.
    webhook_service.emit_evaluation_event(evaluation, event_type, now)


def _blind_label_propensity(evaluation: Evaluation) -> float:
    return evaluation.workspace.settings.calibration_blind_rate


def _routed_event(evaluation, route, routing, now, ctx_actor, audit_sampled, rationale_hash):
    if isinstance(routing, Decided):
        decided_by = "rule" if routing.by == "rule" else "model"
        payload = {
            "route": route,
            "decided_by": decided_by,
            "outcome": routing.outcome,
            "suggested_outcome": routing.outcome if routing.by == "model" else None,
            "raw_confidence": routing.raw_confidence,
            "calibrated_confidence": evaluation.calibrated_confidence,
            "threshold": routing.threshold,
            "automation_policy_id": str(evaluation.automation_policy_id)
            if evaluation.automation_policy_id
            else None,
            "reason": None,
            "blind": False,
            "audit_sampled": audit_sampled,
            "rationale_hash": rationale_hash,
        }
    else:
        payload = {
            "route": route,
            "decided_by": None,
            "outcome": None,
            "suggested_outcome": routing.suggestion,
            "raw_confidence": evaluation.raw_confidence,
            "calibrated_confidence": evaluation.calibrated_confidence,
            "threshold": routing.threshold,
            "automation_policy_id": str(evaluation.automation_policy_id)
            if evaluation.automation_policy_id
            else None,
            "reason": routing.reason,
            "blind": routing.blind,
            "audit_sampled": False,
            "rationale_hash": rationale_hash,
        }
    return Event(
        "evaluation.routed",
        now,
        actor_type=ctx_actor[0],
        actor_id=ctx_actor[1],
        evaluation_id=evaluation.id,
        payload=payload,
    )


def _review_created_event(task, now, ctx_actor) -> Event:
    return Event(
        "review.created",
        now,
        actor_type=ctx_actor[0],
        actor_id=ctx_actor[1],
        evaluation_id=task.evaluation_id,
        subject_type="review_task",
        subject_id=str(task.id),
        payload={
            "review_task_id": str(task.id),
            "kind": task.kind,
            "reason": task.reason,
            "blind": task.blind,
            "label_propensity": task.label_propensity,
            "suggested_outcome": task.suggested_outcome,
        },
    )


# --- submit (SPEC §11.3 steps 1-6) ---


def resolve_active_version(decision):
    from apps.decisions.models import DecisionVersion, VersionStatus

    version = DecisionVersion.objects.filter(decision=decision, status=VersionStatus.ACTIVE).first()
    if version is None:
        raise conflict("no_active_version", "the decision has no active version")
    return version


def submit(
    *,
    decision,
    version,
    ctx,
    input_data: Any,
    external_id: str | None,
    idempotency_key: str | None,
    metadata: Any,
    now: _dt.datetime,
    rng: random.Random | None = None,
) -> SubmitResult:
    """Run §11.3 steps 1-6; returns the evaluation and whether to enqueue."""
    workspace = decision.workspace
    workspace_key = accounts_service.workspace_hmac_key(workspace)
    request_hmac = lookup_hmac(workspace_key, normalise_request(input_data, external_id, metadata))

    # Step 1: idempotency replay / conflict.
    if idempotency_key is not None:
        existing = Evaluation.objects.filter(
            workspace=workspace, decision=decision, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            if existing.request_hmac != request_hmac:
                raise conflict("idempotency_conflict", "idempotency key reused with new request")
            return SubmitResult(evaluation=existing, replayed=True, enqueue=False)

    defn = load_definition(version.definition)

    # Step 3: §5.6 caps + input validation (nothing stored).
    try:
        defn.validate_input(input_data)
    except InputValidationError as exc:
        raise unprocessable(
            "input_invalid",
            "input is invalid",
            details=[{"code": i.code, "path": i.path, "message": i.message} for i in exc.issues],
        ) from exc

    # Step 4: rules; context for the model path.
    rule_result, rule_trace = defn.evaluate_rules(input_data)
    subject_value = _subject_value(defn, input_data)
    subject_hmac = lookup_hmac(workspace_key, subject_value) if subject_value is not None else None

    engine = build_engine(rng=rng)
    core_ctx = EvaluationContext(
        resubmission=_resubmission(defn, workspace, decision, subject_hmac, now),
        budget_exceeded=_budget_exceeded(workspace, now),
        observed_model=version.observed_model,
        blind_rate=workspace.settings.calibration_blind_rate,
    )

    salt = new_salt()
    ctx_actor = (ctx.actor_type, ctx.actor_id)
    # The active policy is applied by the worker after the model runs; rule-only
    # and pre-model-gate paths here never need it.

    try:
        with transaction.atomic():
            evaluation = Evaluation(
                workspace=workspace,
                decision=decision,
                decision_version=version,
                external_id=external_id,
                idempotency_key=idempotency_key,
                request_hmac=request_hmac,
                subject_hmac=subject_hmac,
                salt=salt,
                input=input_data,
                input_hash=content_hash(salt, input_data),
                minimized_input=None,
                minimized_input_hash=None,
                status=EvaluationStatus.PROCESSING,
                state_version=1,
                rule_result={
                    "type": rule_result.type,
                    "matched_row_ids": rule_result.matched_row_ids,
                    "row_id": rule_result.row_id,
                    "outcome": rule_result.outcome,
                },
                rule_trace=rule_trace,
                metadata=metadata,
                created_by_api_key_id=ctx.api_key.id if ctx.is_api_key else None,
                created_at=now,
                updated_at=now,
            )

            from adjudik_core.engine import _rule_handled_without_model

            events: list[Event] = []
            enqueue = False

            if _rule_handled_without_model(defn.definition, rule_result):
                evaluation.save()
                events.append(_received_event(evaluation, now, ctx_actor))
                events.append(_rules_event(evaluation, rule_result, now, ctx_actor))
                routing = engine.route(defn, rule_result, None, None, core_ctx)
                events.extend(
                    apply_routing(
                        evaluation=evaluation,
                        routing=routing,
                        defn=defn,
                        salt=salt,
                        now=now,
                        ctx_actor=ctx_actor,
                    )
                )
            else:
                evaluation.handed_to_model = True
                minimized = engine.minimize_input(defn, input_data)
                evaluation.minimized_input = minimized
                evaluation.minimized_input_hash = content_hash(salt, minimized)
                pre = engine.pre_model_check(defn, minimized, core_ctx)
                evaluation.injection_flags = pre.flags or None
                evaluation.save()
                events.append(_received_event(evaluation, now, ctx_actor))
                events.append(_rules_event(evaluation, rule_result, now, ctx_actor))
                if pre.flags:
                    events.append(
                        Event(
                            "model.screen_flagged",
                            now,
                            actor_type=ctx_actor[0],
                            actor_id=ctx_actor[1],
                            evaluation_id=evaluation.id,
                            payload={
                                "rules": sorted({f["rule"] for f in pre.flags}),
                                "paths": [f["path"] for f in pre.flags],
                            },
                        )
                    )
                if pre.gate_reason is not None:
                    # Pre-model gate: settled now, no model call.
                    events.extend(
                        apply_routing(
                            evaluation=evaluation,
                            routing=Gate(pre.gate_reason),
                            defn=defn,
                            salt=salt,
                            now=now,
                            ctx_actor=ctx_actor,
                        )
                    )
                else:
                    evaluation.model_enqueued_at = now
                    evaluation.save(update_fields=["model_enqueued_at"])
                    enqueue = True
                    # Step 6: enqueue on commit; the sweeper recovers if the
                    # broker is down, so the request still succeeds with 202.
                    import apps.evaluations.tasks as _tasks

                    eid = str(evaluation.id)
                    transaction.on_commit(
                        lambda eid=eid: _tasks.run_model_step.apply_async(args=[eid])
                    )

            audit.append_many(workspace.id, events)
    except IntegrityError:
        # Step 5: unique-violation race on the idempotency key -> replay (step 1).
        existing = Evaluation.objects.filter(
            workspace=workspace, decision=decision, idempotency_key=idempotency_key
        ).first()
        if existing is not None:
            if existing.request_hmac != request_hmac:
                raise conflict(
                    "idempotency_conflict", "idempotency key reused with new request"
                ) from None
            return SubmitResult(evaluation=existing, replayed=True, enqueue=False)
        raise

    return SubmitResult(evaluation=evaluation, replayed=False, enqueue=enqueue)


# --- serialisation (SPEC §11.3) ---


def _active_version_number(evaluation: Evaluation) -> int:
    return evaluation.decision_version.version


def response_body(evaluation: Evaluation, *, sensitive: bool = False) -> dict[str, Any]:
    from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask

    model = None
    if evaluation.handed_to_model and evaluation.suggested_outcome is not None:
        model = {
            "suggested_outcome": evaluation.suggested_outcome,
            "raw_confidence": evaluation.raw_confidence,
            "calibrated_confidence": evaluation.calibrated_confidence,
            "threshold": evaluation.threshold,
        }

    gate = (
        ReviewTask.objects.filter(evaluation=evaluation, kind=ReviewKind.GATE)
        .exclude(status=ReviewStatus.CANCELLED)
        .first()
    )
    review = None
    if gate is not None:
        review = {
            "id": str(gate.id),
            "kind": gate.kind,
            "status": gate.status,
            "due_at": iso_micros(gate.due_at),
        }

    body: dict[str, Any] = {
        "id": str(evaluation.id),
        "decision": evaluation.decision.key,
        "version": _active_version_number(evaluation),
        "external_id": evaluation.external_id,
        "status": evaluation.status,
        "state_version": evaluation.state_version,
        "outcome": evaluation.outcome,
        "decided_by": evaluation.decided_by,
        "route_reason": evaluation.route_reason,
        "rule_result": evaluation.rule_result,
        "model": model,
        "review": review,
        "created_at": iso_micros(evaluation.created_at),
        "decided_at": iso_micros(evaluation.decided_at) if evaluation.decided_at else None,
        "metadata": evaluation.metadata,
    }
    if sensitive:
        body.update(
            {
                "input": evaluation.input,
                "minimized_input": evaluation.minimized_input,
                "rule_trace": evaluation.rule_trace,
                "screen_flags": evaluation.injection_flags or [],
                "model_rationale": evaluation.model_rationale,
                "legal_hold": evaluation.legal_hold,
                "cancelled_at": iso_micros(evaluation.cancelled_at)
                if evaluation.cancelled_at
                else None,
                "cancel_reason": evaluation.cancel_reason,
            }
        )
    return body


def mark_published(evaluation_id) -> None:
    """Register a post-commit wake-up for a waiting client (SPEC §11.4)."""
    transaction.on_commit(lambda: publish_update(evaluation_id))


# --- cancel (SPEC §11.2) ---


def cancel(evaluation_id, *, reason: str, ctx_actor: tuple[str, str | None], now: _dt.datetime):
    """Cancel an evaluation from ``processing`` or ``pending_review`` (SPEC §11.2).

    Locks the evaluation (and so serialises against a concurrent review
    completion, which locks the same row), cancels its open/claimed gate task,
    inserts the ``evaluation.cancelled`` webhook outbox row, and appends the
    cancellation and ``review.cancelled`` events once before commit.
    """
    from apps.reviews import service as reviews_service
    from apps.webhooks import service as webhook_service

    with transaction.atomic():
        evaluation = (
            Evaluation.objects.select_for_update(no_key=True, of=("self",))
            .select_related("decision", "decision_version")
            .get(pk=evaluation_id)
        )
        if evaluation.status not in (
            EvaluationStatus.PROCESSING,
            EvaluationStatus.PENDING_REVIEW,
        ):
            raise conflict(
                "invalid_state", "only a processing or pending-review case can be cancelled"
            )

        evaluation.status = EvaluationStatus.CANCELLED
        evaluation.cancel_reason = reason
        evaluation.cancelled_at = now
        evaluation.state_version += 1
        evaluation.updated_at = now
        evaluation.save()

        events: list[Event] = reviews_service.cancel_gate_tasks(evaluation, now=now)
        events.append(
            Event(
                "evaluation.cancelled",
                now,
                actor_type=ctx_actor[0],
                actor_id=ctx_actor[1],
                evaluation_id=evaluation.id,
                payload={"reason": reason, "state_version": evaluation.state_version},
            )
        )
        webhook_service.emit_cancelled(evaluation, now)
        audit.append_many(evaluation.workspace_id, events)
        mark_published(evaluation.id)
    return evaluation
