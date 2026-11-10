"""Model worker and sweeper (SPEC §11.5).

``run_model_step`` runs the model outside any transaction, persisting each
provider call in its own short transaction, then routes under a fencing-token
guard so a superseded run cannot overwrite a newer one. ``sweep_stuck_evaluations``
re-enqueues or gates stalled evaluations and marks lost model calls.
"""

from __future__ import annotations

import datetime as _dt
import uuid

from celery import shared_task
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from adjudik_core import AutomationPolicy as CoreAutomationPolicy
from adjudik_core import load_definition
from adjudik_core.engine import EvaluationContext, Gate
from apps.common import audit
from apps.common.audit import Event
from apps.common.engine import build_engine
from apps.common.hashing import content_hash

from . import service
from .models import Evaluation, EvaluationStatus, ModelCall, ModelCallStatus

WORKER_SYSTEM_ACTOR = ("system", None)
ADVISORY_SWEEP_KEY = 0x4144_0001  # "AD" + job id


def _core_policy(db_policy):
    if db_policy is None:
        return None
    return CoreAutomationPolicy(
        thresholds=dict(db_policy.thresholds),
        audit_sample_rate=db_policy.audit_sample_rate,
        targets=dict(db_policy.targets) if db_policy.targets else None,
        calibration_map=db_policy.calibration_map,
    )


def _make_callbacks(evaluation: Evaluation, salt: bytes):
    store = settings.ADJUDIK_STORE_PROMPTS
    started: list[str] = []

    def on_call_start(record) -> None:
        now = timezone.now()
        with transaction.atomic():
            ModelCall.objects.create(
                id=uuid.UUID(record.id),
                workspace=evaluation.workspace,
                evaluation=evaluation,
                status=ModelCallStatus.STARTED,
                purpose=record.purpose,
                sample_index=record.sample_index or 0,
                attempt=record.attempt,
                provider=record.provider,
                model_requested=record.model_requested,
                request_hash=record.request_hash,
                system=record.system if store else None,
                user=record.user if store else None,
                created_at=now,
            )
            audit.append_many(
                evaluation.workspace_id,
                [
                    Event(
                        "model.called",
                        now,
                        actor_type="system",
                        evaluation_id=evaluation.id,
                        subject_type="model_call",
                        subject_id=record.id,
                        payload={
                            "model_call_id": record.id,
                            "purpose": record.purpose,
                            "sample_index": record.sample_index,
                            "attempt": record.attempt,
                            "provider": record.provider,
                            "model_requested": record.model_requested,
                            "request_hash": record.request_hash,
                        },
                    )
                ],
            )
        started.append(record.id)

    def on_call_end(record) -> None:
        now = timezone.now()
        parsed = record.parsed_output or {}
        response_hash = (
            content_hash(salt, record.response_text) if record.response_text is not None else None
        )
        with transaction.atomic():
            ModelCall.objects.filter(id=uuid.UUID(record.id)).update(
                status=ModelCallStatus.COMPLETED,
                model_reported=record.model_reported,
                response_text=record.response_text if store else None,
                parsed_output=record.parsed_output if store else None,
                valid=record.valid,
                error_codes=record.error_codes,
                stop_reason=record.stop_reason,
                provider_error_code=record.provider_error_code,
                input_tokens=record.input_tokens,
                output_tokens=record.output_tokens,
                latency_ms=record.latency_ms,
                completed_at=now,
            )
            audit.append_many(
                evaluation.workspace_id,
                [
                    Event(
                        "model.responded",
                        now,
                        actor_type="system",
                        evaluation_id=evaluation.id,
                        subject_type="model_call",
                        subject_id=record.id,
                        payload={
                            "model_call_id": record.id,
                            "valid": record.valid,
                            "stop_reason": record.stop_reason,
                            "error_codes": record.error_codes,
                            "provider_error_code": record.provider_error_code,
                            "outcome": parsed.get("outcome"),
                            "raw_confidence": parsed.get("confidence"),
                            "model_reported": record.model_reported,
                            "response_hash": response_hash,
                            "input_tokens": record.input_tokens,
                            "output_tokens": record.output_tokens,
                            "latency_ms": record.latency_ms,
                        },
                    )
                ],
            )

    return on_call_start, on_call_end, started


def process_model_step(evaluation_id, *, engine=None) -> None:
    """Run the model step for one evaluation under a fencing guard (SPEC §11.5)."""
    # Step 1: claim the run and take the fencing token.
    with connection.cursor() as cur:
        cur.execute(
            "UPDATE evaluation SET model_attempts = model_attempts + 1, "
            "model_started_at = now() WHERE id = %s AND status = 'processing' "
            "RETURNING model_attempts",
            [str(evaluation_id)],
        )
        row = cur.fetchone()
    if row is None:
        return
    token = row[0]

    evaluation = Evaluation.objects.select_related("workspace", "decision", "decision_version").get(
        pk=evaluation_id
    )
    defn = load_definition(evaluation.decision_version.definition)
    salt = bytes(evaluation.salt)
    minimized = evaluation.minimized_input
    engine = engine or build_engine()

    on_call_start, on_call_end, _started = _make_callbacks(evaluation, salt)

    def should_abort() -> bool:
        return (
            Evaluation.objects.values_list("status", flat=True).get(pk=evaluation_id)
            != EvaluationStatus.PROCESSING
        )

    # Step 2: run the model outside any transaction.
    model_result = engine.run_model_step(
        defn,
        minimized,
        deadline=engine.clock.monotonic() + engine.step_deadline_s,
        on_call_start=on_call_start,
        on_call_end=on_call_end,
        should_abort=should_abort,
    )

    # Steps 3-4: route under the status/token guard.
    route_under_guard(evaluation_id, token, model_result, engine=engine)


def route_under_guard(evaluation_id, token: int, model_result, *, engine=None) -> bool:
    """Route a finished model run only if it is still the current one (SPEC §11.5 step 3).

    Returns ``True`` if this run routed the evaluation, ``False`` if it was
    superseded (stale fencing token) or the evaluation already left ``processing``.
    """
    engine = engine or build_engine()
    with transaction.atomic():
        evaluation = (
            Evaluation.objects.select_for_update(of=("self",))
            .select_related("workspace", "decision", "decision_version")
            .get(pk=evaluation_id)
        )
        if evaluation.status != EvaluationStatus.PROCESSING or evaluation.model_attempts != token:
            ModelCall.objects.filter(evaluation=evaluation, status=ModelCallStatus.STARTED).update(
                superseded=True
            )
            return False

        from apps.decisions.models import AutomationPolicy as DbPolicy
        from apps.decisions.models import DecisionVersion, PolicyStatus

        defn = load_definition(evaluation.decision_version.definition)
        salt = bytes(evaluation.salt)
        version = DecisionVersion.objects.get(pk=evaluation.decision_version_id)
        db_policy = DbPolicy.objects.filter(
            decision_version=version, status=PolicyStatus.ACTIVE
        ).first()
        core_ctx = EvaluationContext(
            observed_model=version.observed_model,
            blind_rate=evaluation.workspace.settings.calibration_blind_rate,
        )
        rule_result, _trace = defn.evaluate_rules(evaluation.input)
        routing = engine.route(defn, rule_result, model_result, _core_policy(db_policy), core_ctx)

        now = timezone.now()
        calibrated = None
        if db_policy is not None and db_policy.calibration_map and model_result.raw_confidence:
            calibrated = _core_policy(db_policy).calibrated(model_result.raw_confidence)

        events = service.apply_routing(
            evaluation=evaluation,
            routing=routing,
            defn=defn,
            salt=salt,
            now=now,
            ctx_actor=WORKER_SYSTEM_ACTOR,
            rationale=model_result.rationale,
            automation_policy_id=db_policy.id if db_policy is not None else None,
            calibrated_confidence=calibrated,
        )

        # Record the first observed model for the version (SPEC §8.2).
        if model_result.model_reported and version.observed_model is None:
            version.observed_model = model_result.model_reported
            version.save(update_fields=["observed_model"])

        audit.append_many(evaluation.workspace_id, events)
        service.mark_published(evaluation.id)
        return True


def gate_internal_error(evaluation_id, *, now=None) -> None:
    """Send an evaluation to a gate review with reason ``internal_error`` (SPEC §11.5)."""
    now = now or timezone.now()
    with transaction.atomic():
        evaluation = Evaluation.objects.select_for_update(of=("self",)).get(pk=evaluation_id)
        if evaluation.status != EvaluationStatus.PROCESSING:
            return
        defn = load_definition(evaluation.decision_version.definition)
        salt = bytes(evaluation.salt)
        events = service.apply_routing(
            evaluation=evaluation,
            routing=Gate("internal_error"),
            defn=defn,
            salt=salt,
            now=now,
            ctx_actor=WORKER_SYSTEM_ACTOR,
        )
        audit.append_many(evaluation.workspace_id, events)
        service.mark_published(evaluation.id)


@shared_task(
    bind=True,
    queue="model",
    acks_late=True,
    max_retries=1,
    soft_time_limit=120,
    time_limit=150,
)
def run_model_step(self, evaluation_id) -> None:  # pragma: no cover - thin celery wrapper
    from celery.exceptions import SoftTimeLimitExceeded

    try:
        process_model_step(evaluation_id)
    except SoftTimeLimitExceeded:
        gate_internal_error(evaluation_id)
        raise
    except Exception as exc:
        if self.request.retries >= 1:
            gate_internal_error(evaluation_id)
            return
        raise self.retry(countdown=5, exc=exc) from exc


def enqueue_model_step(evaluation_id) -> None:
    """Register the worker enqueue on commit; the sweeper recovers if it is lost."""
    transaction.on_commit(lambda: run_model_step.apply_async(args=[str(evaluation_id)]))


# --- sweeper (SPEC §11.5) ---


def sweep_stuck_evaluations(*, now: _dt.datetime | None = None) -> dict[str, int]:
    """Re-enqueue or gate stalled evaluations; mark lost model calls (SPEC §11.5)."""
    now = now or timezone.now()
    reenqueued = gated = lost = 0

    stuck = Evaluation.objects.filter(status=EvaluationStatus.PROCESSING)
    for evaluation in stuck:
        enqueued_old = (
            evaluation.model_started_at is None
            and evaluation.model_enqueued_at is not None
            and (now - evaluation.model_enqueued_at) > _dt.timedelta(seconds=60)
        )
        started_old = evaluation.model_started_at is not None and (
            now - evaluation.model_started_at
        ) > _dt.timedelta(minutes=5)
        if not (enqueued_old or started_old):
            continue
        if evaluation.model_attempts >= 3:
            gate_internal_error(evaluation.id, now=now)
            gated += 1
        else:
            enqueue_model_step(evaluation.id)
            reenqueued += 1

    lost_cutoff = now - _dt.timedelta(minutes=5)
    lost_calls = ModelCall.objects.filter(
        status=ModelCallStatus.STARTED, created_at__lt=lost_cutoff
    ).select_related("evaluation")
    for call in lost_calls:
        with transaction.atomic():
            ModelCall.objects.filter(pk=call.pk).update(
                status=ModelCallStatus.LOST,
                provider_error_code="worker_lost",
                completed_at=now,
            )
            audit.append_many(
                call.workspace_id,
                [
                    Event(
                        "model.responded",
                        now,
                        actor_type="system",
                        evaluation_id=call.evaluation_id,
                        subject_type="model_call",
                        subject_id=str(call.id),
                        payload={
                            "model_call_id": str(call.id),
                            "valid": False,
                            "stop_reason": None,
                            "error_codes": [],
                            "provider_error_code": "worker_lost",
                            "outcome": None,
                            "raw_confidence": None,
                            "model_reported": None,
                            "response_hash": None,
                            "input_tokens": None,
                            "output_tokens": None,
                            "latency_ms": None,
                        },
                    )
                ],
            )
        lost += 1

    return {"reenqueued": reenqueued, "gated": gated, "lost": lost}


@shared_task(queue="jobs", ignore_result=True)
def sweep_stuck_evaluations_task() -> dict[str, int] | None:  # pragma: no cover - celery wrapper
    with connection.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(%s)", [ADVISORY_SWEEP_KEY])
        if not cur.fetchone()[0]:
            return None
    try:
        return sweep_stuck_evaluations()
    finally:
        with connection.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [ADVISORY_SWEEP_KEY])
