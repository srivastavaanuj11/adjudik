"""Review queue: claim, heartbeat, release and complete (SPEC §8.4-§8.6).

Lock order everywhere is ``decision`` → ``evaluation`` → ``review_task``; row
locks on ``evaluation`` and ``review_task`` use ``FOR NO KEY UPDATE``
(``select_for_update(no_key=True)``). Every transactional operation appends its
audit events once, as the last statement before commit (SPEC §9.3), and inserts
webhook outbox rows in the same transaction (SPEC §11.6).

Blind redaction (SPEC §8.6): a reviewer working a blind task never sees the
suggested outcome, confidence, the evaluation's outcome/decided_by/route_reason
or the model rationale — the serializers omit them.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from django.db import transaction
from django.db.models import Q

from adjudik_core import load_definition
from apps.common import audit
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.errors import conflict, forbidden, not_found, unprocessable
from apps.common.hashing import content_hash
from apps.evaluations.models import DecidedBy, Evaluation, EvaluationStatus
from apps.webhooks import service as webhook_service

from .models import ClaimVia, ReviewAction, ReviewKind, ReviewStatus, ReviewTask

MAX_REASON_TEXT = 2000


# --- reviewer-visible input (SPEC §5.6, §8.6) ---


def reviewer_visible_input(input_schema: dict[str, Any], data: Any) -> tuple[Any, list[str]]:
    """Strip ``x-adjudik-reviewer-hidden`` fields; return visible input + leaf paths.

    Reviewers see the full input except properties the schema marks hidden
    (SPEC §5.6). ``included_paths`` lists the dotted paths of the visible leaves.
    """
    paths: list[str] = []
    visible = _visit(data, input_schema, input_schema or {}, "", paths)
    return visible, paths


def _deref(schema: Any, root: dict[str, Any]) -> Any:
    if isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if isinstance(ref, str) and ref.startswith("#/"):
            node: Any = root
            for part in ref[2:].split("/"):
                if not isinstance(node, dict):
                    return schema
                node = node.get(part)
            return node if isinstance(node, dict) else schema
    return schema


def _visit(value: Any, schema: Any, root: dict[str, Any], path: str, paths: list[str]) -> Any:
    schema = _deref(schema, root) if isinstance(schema, dict) else None
    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema, dict) else None
        out: dict[str, Any] = {}
        for key, child in value.items():
            child_schema = props.get(key) if isinstance(props, dict) else None
            if isinstance(child_schema, dict) and child_schema.get("x-adjudik-reviewer-hidden"):
                continue
            child_path = f"{path}.{key}" if path else key
            out[key] = _visit(child, child_schema, root, child_path, paths)
        return out
    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        return [_visit(v, items, root, f"{path}[{i}]", paths) for i, v in enumerate(value)]
    if path:
        paths.append(path)
    return value


# --- lookups ---


def resolve_task(ctx, task_id) -> ReviewTask:
    task = (
        ReviewTask.objects.select_related("evaluation", "decision")
        .filter(workspace=ctx.workspace, id=task_id)
        .first()
    )
    if task is None or not ctx.can_see_decision(task.decision_id):
        raise not_found("review")
    return task


def _definition(task: ReviewTask):
    return load_definition(task.evaluation.decision_version.definition)


def _suggestion_visible(task: ReviewTask) -> bool:
    return task.suggested_outcome is not None and not task.blind


# --- next / claim (SPEC §8.5) ---


def _lease_until(task: ReviewTask, now: _dt.datetime) -> _dt.datetime:
    minutes = task.evaluation.decision_version.definition["review"].get("claim_lease_minutes", 15)
    return now + _dt.timedelta(minutes=minutes)


def _own_unexpired_claim(ctx, now: _dt.datetime) -> ReviewTask | None:
    return (
        ReviewTask.objects.select_related("evaluation", "decision")
        .filter(
            workspace=ctx.workspace,
            claimed_by_id=ctx.actor_id,
            status=ReviewStatus.CLAIMED,
            lease_expires_at__gt=now,
        )
        .first()
    )


def _release_own_expired(ctx, now: _dt.datetime) -> list[Event]:
    """Return the caller's own expired claims to ``open`` so the one-claim
    invariant holds before a fresh claim (SPEC §8.4)."""
    events: list[Event] = []
    expired = ReviewTask.objects.filter(
        workspace=ctx.workspace,
        claimed_by_id=ctx.actor_id,
        status=ReviewStatus.CLAIMED,
        lease_expires_at__lte=now,
    )
    for task in expired:
        task.status = ReviewStatus.OPEN
        task.claimed_by = None
        task.claimed_at = None
        task.claim_via = None
        task.lease_expires_at = None
        task.save(
            update_fields=["status", "claimed_by", "claimed_at", "claim_via", "lease_expires_at"]
        )
        events.append(_released_event(task, now, ctx, by="lease_expiry"))
    return events


def claim_next(ctx, *, decision_key: str | None, kind: str | None, now: _dt.datetime):
    """Claim the caller's own task or the next eligible one (SPEC §8.5).

    Returns the claimed :class:`ReviewTask`, or ``None`` when the queue is empty.
    """
    existing = _own_unexpired_claim(ctx, now)
    if existing is not None:
        return existing

    decision_id = _resolve_decision_id(ctx, decision_key)
    with transaction.atomic():
        events = _release_own_expired(ctx, now)
        candidates = (
            ReviewTask.objects.select_for_update(skip_locked=True, no_key=True, of=("self",))
            .filter(workspace=ctx.workspace)
            .filter(
                Q(status=ReviewStatus.OPEN)
                | Q(status=ReviewStatus.CLAIMED, lease_expires_at__lte=now)
            )
            .order_by("priority", "due_at", "created_at")
        )
        if decision_id is not None:
            candidates = candidates.filter(decision_id=decision_id)
        if kind is not None:
            candidates = candidates.filter(kind=kind)
        task = candidates.first()
        if task is None:
            if events:
                audit.append_many(ctx.workspace.id, events)
            return None
        task = _assign(task, ctx, ClaimVia.NEXT, now)
        events.append(_claimed_event(task, now, ctx))
        audit.append_many(ctx.workspace.id, events)
    task.refresh_from_db()
    task = ReviewTask.objects.select_related(
        "evaluation", "decision", "evaluation__decision_version"
    ).get(pk=task.id)
    return task


def claim_by_id(ctx, task_id, *, now: _dt.datetime) -> ReviewTask:
    """Claim a specific task by id (SPEC §8.5); ``next_only`` forbids it for reviewers."""
    if (
        ctx.actor_type == "user"
        and ctx.role == "reviewer"
        and ctx.workspace.settings.reviewer_claim_mode == "next_only"
    ):
        raise forbidden("claim by id is disabled (reviewer_claim_mode=next_only)")

    with transaction.atomic():
        task = (
            ReviewTask.objects.select_for_update(no_key=True, of=("self",))
            .select_related("evaluation", "decision", "evaluation__decision_version")
            .filter(workspace=ctx.workspace, id=task_id)
            .first()
        )
        if task is None or not ctx.can_see_decision(task.decision_id):
            raise not_found("review")
        if task.status == ReviewStatus.CANCELLED:
            raise conflict("invalid_state", "the task is cancelled")
        if task.status == ReviewStatus.COMPLETED:
            raise conflict("invalid_state", "the task is already completed")
        if (
            task.status == ReviewStatus.CLAIMED
            and task.lease_expires_at is not None
            and task.lease_expires_at > now
        ):
            if str(task.claimed_by_id) == str(ctx.actor_id):
                return task
            raise conflict("already_claimed", "the task is claimed by another reviewer")

        events = _release_own_expired(ctx, now)
        task = _assign(task, ctx, ClaimVia.DIRECT, now)
        events.append(_claimed_event(task, now, ctx))
        audit.append_many(ctx.workspace.id, events)
    return task


def _assign(task: ReviewTask, ctx, via: str, now: _dt.datetime) -> ReviewTask:
    task.status = ReviewStatus.CLAIMED
    task.claimed_by_id = ctx.actor_id
    task.claimed_at = now
    task.claim_via = via
    task.lease_expires_at = _lease_until(task, now)
    task.save(update_fields=["status", "claimed_by", "claimed_at", "claim_via", "lease_expires_at"])
    return task


# --- heartbeat / release (SPEC §8.5) ---


def heartbeat(ctx, task_id, *, now: _dt.datetime) -> ReviewTask:
    with transaction.atomic():
        task = _lock_task(ctx, task_id)
        if task.status == ReviewStatus.CANCELLED:
            raise conflict("invalid_state", "the task is cancelled")
        _require_live_claim(task, ctx, now)
        task.lease_expires_at = _lease_until(task, now)
        task.save(update_fields=["lease_expires_at"])
    return task


def release(ctx, task_id, *, now: _dt.datetime) -> ReviewTask:
    with transaction.atomic():
        task = _lock_task(ctx, task_id)
        if task.status != ReviewStatus.CLAIMED or str(task.claimed_by_id) != str(ctx.actor_id):
            raise conflict("lease_lost", "you do not hold this task")
        task.status = ReviewStatus.OPEN
        task.claimed_by = None
        task.claimed_at = None
        task.claim_via = None
        task.lease_expires_at = None
        task.save(
            update_fields=["status", "claimed_by", "claimed_at", "claim_via", "lease_expires_at"]
        )
        audit.append_many(ctx.workspace.id, [_released_event(task, now, ctx, by="user")])
    return task


def _lock_task(ctx, task_id) -> ReviewTask:
    task = (
        ReviewTask.objects.select_for_update(no_key=True, of=("self",))
        .select_related("evaluation", "decision", "evaluation__decision_version")
        .filter(workspace=ctx.workspace, id=task_id)
        .first()
    )
    if task is None or not ctx.can_see_decision(task.decision_id):
        raise not_found("review")
    return task


def _require_live_claim(task: ReviewTask, ctx, now: _dt.datetime) -> None:
    if (
        task.status != ReviewStatus.CLAIMED
        or str(task.claimed_by_id) != str(ctx.actor_id)
        or task.lease_expires_at is None
        or task.lease_expires_at <= now
    ):
        raise conflict("lease_lost", "your lease on this task has expired")


# --- complete (SPEC §8.5) ---


def complete(ctx, task_id, body: dict[str, Any], *, now: _dt.datetime) -> ReviewTask:
    action = body.get("action")
    outcome = body.get("outcome")
    reason_codes = body.get("reason_codes") or []
    reason_text = body.get("reason_text")
    notes = body.get("notes")

    if action not in (ReviewAction.ACCEPT, ReviewAction.OVERRIDE, ReviewAction.DECIDE):
        raise unprocessable("review_invalid", "action must be accept, override or decide")
    if not isinstance(reason_codes, list):
        raise unprocessable("review_invalid", "reason_codes must be a list")
    if reason_text is not None and (
        not isinstance(reason_text, str) or len(reason_text) > MAX_REASON_TEXT
    ):
        raise unprocessable("review_invalid", "reason_text must be <= 2000 chars")

    with transaction.atomic():
        # Lock order decision -> evaluation -> review_task (SPEC §8.5): read the
        # task unlocked to find its evaluation, lock the evaluation, then the task.
        head = (
            ReviewTask.objects.select_related("evaluation", "decision")
            .filter(workspace=ctx.workspace, id=task_id)
            .first()
        )
        if head is None or not ctx.can_see_decision(head.decision_id):
            raise not_found("review")
        evaluation = (
            Evaluation.objects.select_for_update(no_key=True, of=("self",))
            .select_related("decision", "decision_version")
            .get(pk=head.evaluation_id)
        )
        task = (
            ReviewTask.objects.select_for_update(no_key=True, of=("self",))
            .select_related("evaluation", "decision", "evaluation__decision_version")
            .get(pk=task_id)
        )
        _require_live_claim(task, ctx, now)
        if task.kind == ReviewKind.GATE and evaluation.status != EvaluationStatus.PENDING_REVIEW:
            raise conflict("invalid_state", "the evaluation is not pending review")

        defn = load_definition(evaluation.decision_version.definition)
        self_visible = _suggestion_visible(task)
        outcome = _validate_completion(
            defn, task, action, outcome, reason_text, reason_codes, self_visible
        )

        salt = bytes(evaluation.salt) if evaluation.salt is not None else b""
        agrees = None
        if task.suggested_outcome is not None:
            agrees = outcome == task.suggested_outcome

        task.status = ReviewStatus.COMPLETED
        task.completed_by_id = ctx.actor_id
        task.completed_at = now
        task.action = action
        task.final_outcome = outcome
        task.agrees_with_model = agrees
        task.reason_codes = reason_codes
        task.reason_text = reason_text
        task.notes = notes
        task.save()

        events: list[Event] = [_completed_event(task, salt, now, ctx)]

        if task.kind == ReviewKind.GATE:
            evaluation.status = EvaluationStatus.DECIDED
            evaluation.decided_by = DecidedBy.HUMAN
            evaluation.outcome = outcome
            evaluation.state_version += 1
            evaluation.decided_at = now
            evaluation.updated_at = now
            evaluation.save()
            events.append(
                Event(
                    "evaluation.decided",
                    now,
                    actor_type=ctx.actor_type,
                    actor_id=ctx.actor_id,
                    evaluation_id=evaluation.id,
                    payload={
                        "outcome": evaluation.outcome,
                        "decided_by": evaluation.decided_by,
                        "state_version": evaluation.state_version,
                    },
                )
            )
            webhook_service.emit_decided(evaluation, now)
        else:  # audit task; the evaluation is unchanged
            if agrees is False:
                events.append(
                    Event(
                        "audit.disagreement",
                        now,
                        actor_type=ctx.actor_type,
                        actor_id=ctx.actor_id,
                        evaluation_id=evaluation.id,
                        subject_type="review_task",
                        subject_id=str(task.id),
                        payload={
                            "review_task_id": str(task.id),
                            "evaluation_outcome": evaluation.suggested_outcome,
                            "reviewer_outcome": outcome,
                        },
                    )
                )
                webhook_service.emit_audit_disagreement(evaluation, task, now)
                # Circuit breaker (§8.8) runs here once M5 lands.

        audit.append_many(ctx.workspace.id, events)
        transaction.on_commit(lambda eid=evaluation.id: _wake(eid))
    return task


def _wake(evaluation_id) -> None:
    from apps.evaluations.waiting import publish_update

    publish_update(evaluation_id)


def _validate_completion(defn, task, action, outcome, reason_text, reason_codes, visible) -> str:
    outcome_ids = {o.id for o in defn.definition.outcomes}
    if action == ReviewAction.DECIDE:
        if visible:
            raise unprocessable(
                "review_invalid", "use accept or override when a suggestion is shown"
            )
    else:  # accept / override
        if not visible:
            raise unprocessable("review_invalid", "decide is required when no suggestion is shown")

    if action == ReviewAction.ACCEPT:
        if outcome is None:
            outcome = task.suggested_outcome
        if outcome != task.suggested_outcome:
            raise unprocessable("review_invalid", "accept must keep the suggested outcome")
    elif action == ReviewAction.OVERRIDE:
        if outcome == task.suggested_outcome:
            raise unprocessable("review_invalid", "override must change the outcome")
        if not reason_text:
            raise unprocessable("review_invalid", "override requires reason_text")
    # decide
    if not isinstance(outcome, str) or outcome not in outcome_ids:
        raise unprocessable("review_invalid", "outcome must be a declared outcome id")

    declared = [c.id for c in defn.definition.review.reason_codes]
    if declared:
        if not reason_codes:
            raise unprocessable("review_invalid", "at least one reason code is required")
        for code in reason_codes:
            if code not in declared:
                raise unprocessable("review_invalid", "unknown reason code")
    return outcome


# --- cancellation of an evaluation's gate task (SPEC §8.4) ---


def cancel_gate_tasks(evaluation, *, now: _dt.datetime) -> list[Event]:
    """Cancel an evaluation's open/claimed gate tasks; return audit events.

    Called inside the cancel transaction (SPEC §11.2); the caller appends.
    """
    events: list[Event] = []
    tasks = ReviewTask.objects.filter(
        evaluation=evaluation,
        kind=ReviewKind.GATE,
        status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED],
    )
    for task in tasks:
        task.status = ReviewStatus.CANCELLED
        task.cancelled_at = now
        task.save(update_fields=["status", "cancelled_at"])
        events.append(
            Event(
                "review.cancelled",
                now,
                actor_type="system",
                evaluation_id=evaluation.id,
                subject_type="review_task",
                subject_id=str(task.id),
                payload={"review_task_id": str(task.id)},
            )
        )
    return events


# --- events ---


def _claimed_event(task: ReviewTask, now, ctx) -> Event:
    return Event(
        "review.claimed",
        now,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        evaluation_id=task.evaluation_id,
        subject_type="review_task",
        subject_id=str(task.id),
        payload={
            "review_task_id": str(task.id),
            "via": task.claim_via,
            "lease_expires_at": iso_micros(task.lease_expires_at),
        },
    )


def _released_event(task: ReviewTask, now, ctx, *, by: str) -> Event:
    return Event(
        "review.released",
        now,
        actor_type=ctx.actor_type if by == "user" else "system",
        actor_id=ctx.actor_id if by == "user" else None,
        evaluation_id=task.evaluation_id,
        subject_type="review_task",
        subject_id=str(task.id),
        payload={"review_task_id": str(task.id), "by": by},
    )


def _completed_event(task: ReviewTask, salt: bytes, now, ctx) -> Event:
    return Event(
        "review.completed",
        now,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        evaluation_id=task.evaluation_id,
        subject_type="review_task",
        subject_id=str(task.id),
        payload={
            "review_task_id": str(task.id),
            "action": task.action,
            "final_outcome": task.final_outcome,
            "agrees_with_model": task.agrees_with_model,
            "reason_codes": task.reason_codes,
            "reason_text_hash": content_hash(salt, task.reason_text)
            if task.reason_text is not None
            else None,
            "notes_hash": content_hash(salt, task.notes) if task.notes is not None else None,
        },
    )


# --- serialisation (SPEC §8.6, §11.8) ---


def _resolve_decision_id(ctx, decision_key: str | None):
    if not decision_key:
        return None
    from apps.decisions.models import Decision

    decision = Decision.objects.filter(workspace=ctx.workspace, key=decision_key).first()
    if decision is None or not ctx.can_see_decision(decision.id):
        raise not_found("decision")
    return decision.id


def summary(task: ReviewTask) -> dict[str, Any]:
    """Queue-row summary. Blind fields and the suggestion are omitted (SPEC §8.6)."""
    claim_mode = task.workspace.settings.reviewer_claim_mode
    show_external = not (claim_mode == "next_only" and task.status == ReviewStatus.OPEN)
    body = {
        "id": str(task.id),
        "kind": task.kind,
        "reason": task.reason,
        "status": task.status,
        "blind": task.blind,
        "priority": task.priority,
        "decision": task.decision.key,
        "external_id": task.evaluation.external_id if show_external else None,
        "due_at": iso_micros(task.due_at),
        "lease_expires_at": iso_micros(task.lease_expires_at) if task.lease_expires_at else None,
        "created_at": iso_micros(task.created_at),
    }
    if not task.blind and task.suggested_outcome is not None:
        body["suggested_outcome"] = task.suggested_outcome
    return body


def detail(task: ReviewTask) -> dict[str, Any]:
    """Full review view with blind redaction (SPEC §8.6, §11.8)."""
    evaluation = task.evaluation
    version = evaluation.decision_version
    defn = load_definition(version.definition)
    settings_obj = task.workspace.settings
    claim_mode = settings_obj.reviewer_claim_mode
    show_external = not (claim_mode == "next_only" and task.status == ReviewStatus.OPEN)

    visible_input, included_paths = reviewer_visible_input(
        version.definition.get("input_schema", {}), evaluation.input or {}
    )

    decision_block = {
        "key": task.decision.key,
        "name": task.decision.name,
        "version": version.version,
        "outcomes": [{"id": o.id, "label": o.label} for o in defn.definition.outcomes],
        "reason_codes": [
            {"id": c.id, "label": c.label} for c in defn.definition.review.reason_codes
        ],
    }

    suggestion = None
    if _suggestion_visible(task):
        suggestion = {"outcome": task.suggested_outcome, "rationale": evaluation.model_rationale}

    prior = []
    if task.reason == "resubmission" and evaluation.subject_hmac:
        prior_qs = (
            Evaluation.objects.filter(
                workspace=task.workspace,
                decision=task.decision,
                subject_hmac=evaluation.subject_hmac,
            )
            .exclude(pk=evaluation.pk)
            .order_by("-created_at")[:10]
        )
        prior = [
            {"id": str(e.id), "created_at": iso_micros(e.created_at), "status": e.status}
            for e in prior_qs
        ]

    body: dict[str, Any] = {
        "id": str(task.id),
        "kind": task.kind,
        "reason": task.reason,
        "status": task.status,
        "blind": task.blind,
        "lease_expires_at": iso_micros(task.lease_expires_at) if task.lease_expires_at else None,
        "due_at": iso_micros(task.due_at),
        "decision": decision_block,
        "case": {
            "input": visible_input,
            "included_paths": included_paths,
            "external_id": evaluation.external_id if show_external else None,
        },
        "rules": {
            "inputs": [
                {"id": i.id, "expr": i.expr}
                for i in (defn.definition.rules.inputs if defn.definition.rules else [])
            ],
            "rows": [r.id for r in (defn.definition.rules.rows if defn.definition.rules else [])],
            "trace": evaluation.rule_trace,
        },
        "suggestion": suggestion,
        "prior_evaluations": prior,
    }
    return body
