"""Evaluate and evaluation-read endpoints (SPEC §11.3, §11.4, §11.2).

``POST /decisions/{key}/evaluate`` needs the ``evaluate`` capability. Reads need
``read``; sensitive fields (input, trace, rationale, model calls) need
``read_sensitive`` and write ``data.accessed``. A key scoped to ``decision_ids``
gets ``404`` for other decisions, and a key with only ``evaluate``/``read`` sees
only the evaluations it created.
"""

from __future__ import annotations

import json

from django.conf import settings
from django.utils import timezone
from rest_framework.response import Response

from apps.common import audit, permissions
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.errors import conflict, forbidden, not_found, unprocessable
from apps.common.pagination import paginate
from apps.common.throttling import EvaluateKeyThrottle, WorkspaceEvalThrottle
from apps.common.views import AdjudikAPIView

from . import retention, service
from .models import CancelReason, Evaluation, EvaluationStatus
from .waiting import wait_for_result

MAX_WAIT_MS = 25000


def _resolve_decision(ctx, key):
    from apps.decisions.models import Decision

    decision = Decision.objects.filter(workspace=ctx.workspace, key=key).first()
    if decision is None or not ctx.can_see_decision(decision.id):
        raise not_found("decision")
    return decision


class EvaluateView(AdjudikAPIView):
    # Evaluate has its own per-key and per-workspace limits (SPEC §15.8).
    throttle_classes = [EvaluateKeyThrottle, WorkspaceEvalThrottle]

    def post(self, request, key):
        permissions.require(self.ctx, "evaluate")
        decision = _resolve_decision(self.ctx, key)
        data = request.data if isinstance(request.data, dict) else {}
        if "input" not in data:
            raise unprocessable("input_invalid", "input is required")
        input_data = data["input"]

        external_id = data.get("external_id")
        idempotency_key = data.get("idempotency_key") or request.headers.get("Idempotency-Key")
        metadata = data.get("metadata")
        for label, value in (("external_id", external_id), ("idempotency_key", idempotency_key)):
            if value is not None and (not isinstance(value, str) or len(value) > 200):
                raise unprocessable("input_invalid", f"{label} must be a string of <= 200 chars")
        if metadata is not None:
            if not isinstance(metadata, dict):
                raise unprocessable("input_invalid", "metadata must be an object")
            if len(json.dumps(metadata).encode("utf-8")) > 4096:
                raise unprocessable("input_invalid", "metadata exceeds 4 KB")

        wait = data.get("wait", True)
        wait_ms = data.get("wait_ms", settings.ADJUDIK_DEFAULT_WAIT_MS)
        if not isinstance(wait_ms, int) or not (0 <= wait_ms <= MAX_WAIT_MS):
            raise unprocessable("input_invalid", "wait_ms must be between 0 and 25000")

        version = service.resolve_active_version(decision)
        result = service.submit(
            decision=decision,
            version=version,
            ctx=self.ctx,
            input_data=input_data,
            external_id=external_id,
            idempotency_key=idempotency_key,
            metadata=metadata,
            now=timezone.now(),
        )
        evaluation = result.evaluation

        if wait and evaluation.status == "processing":
            api_key_id = str(self.ctx.api_key.id) if self.ctx.is_api_key else None
            wait_for_result(evaluation, wait_ms, api_key_id)
            evaluation = Evaluation.objects.select_related("decision", "decision_version").get(
                pk=evaluation.id
            )

        body = service.response_body(evaluation)
        http_status = 200 if evaluation.status in ("decided", "cancelled") else 202
        response = Response(body, status=http_status)
        if result.replayed:
            response["Idempotent-Replayed"] = "true"
        return response


class EvaluationsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "read")
        qs = Evaluation.objects.select_related("decision", "decision_version").filter(
            workspace=self.ctx.workspace
        )
        if self.ctx.decision_ids is not None:
            qs = qs.filter(decision_id__in=self.ctx.decision_ids)
        # A key with only evaluate/read sees the evaluations it created (SPEC §11.2).
        if self.ctx.is_api_key and not permissions.has_capability(self.ctx, "read_sensitive"):
            qs = qs.filter(created_by_api_key_id=self.ctx.api_key.id)

        params = request.query_params
        if params.get("decision"):
            qs = qs.filter(decision__key=params["decision"])
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("decided_by"):
            qs = qs.filter(decided_by=params["decided_by"])
        if params.get("route_reason"):
            qs = qs.filter(route_reason=params["route_reason"])
        if params.get("external_id"):
            qs = qs.filter(external_id=params["external_id"])
        if params.get("created_after"):
            qs = qs.filter(created_at__gte=params["created_after"])
        if params.get("created_before"):
            qs = qs.filter(created_at__lt=params["created_before"])

        rows, next_cursor = paginate(qs, request)
        return Response(
            {"results": [service.response_body(e) for e in rows], "next_cursor": next_cursor}
        )


class EvaluationDetailView(AdjudikAPIView):
    def get(self, request, eval_id):
        permissions.require(self.ctx, "read")
        evaluation = (
            Evaluation.objects.select_related("decision", "decision_version")
            .filter(workspace=self.ctx.workspace, id=eval_id)
            .first()
        )
        if evaluation is None or not self.ctx.can_see_decision(evaluation.decision_id):
            raise not_found("evaluation")
        if (
            self.ctx.is_api_key
            and not permissions.has_capability(self.ctx, "read_sensitive")
            and evaluation.created_by_api_key_id != self.ctx.api_key.id
        ):
            raise not_found("evaluation")

        sensitive = permissions.has_capability(self.ctx, "read_sensitive")
        if sensitive:
            self._record_access(evaluation)
        return Response(service.response_body(evaluation, sensitive=sensitive))

    def _record_access(self, evaluation) -> None:
        from django.db import transaction

        now = timezone.now()
        with transaction.atomic():
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "data.accessed",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        evaluation_id=evaluation.id,
                        payload={"evaluation_id": str(evaluation.id), "fields": ["input"]},
                    )
                ],
            )


class EvaluationEventsView(AdjudikAPIView):
    """Audit timeline for one evaluation (SPEC §11.2, read)."""

    def get(self, request, eval_id):
        permissions.require(self.ctx, "read")
        evaluation = _resolve_evaluation(self.ctx, eval_id)
        from apps.audit.models import AuditEvent

        events = AuditEvent.objects.filter(
            workspace_id=self.ctx.workspace.id, evaluation_id=evaluation.id
        ).order_by("seq")
        return Response(
            {
                "results": [
                    {
                        "seq": e.seq,
                        "event_type": e.event_type,
                        "occurred_at": iso_micros(e.occurred_at),
                        "actor_type": e.actor_type,
                        "actor_id": e.actor_id,
                        "subject_type": e.subject_type,
                        "subject_id": e.subject_id,
                        "payload": e.payload,
                    }
                    for e in events
                ]
            }
        )


class EvaluationModelCallsView(AdjudikAPIView):
    """Provider calls for one evaluation (SPEC §11.2, sensitive)."""

    def get(self, request, eval_id):
        permissions.require(self.ctx, "read_sensitive")
        evaluation = _resolve_evaluation(self.ctx, eval_id)
        from django.db import transaction

        from .models import ModelCall

        calls = ModelCall.objects.filter(evaluation=evaluation).order_by("created_at")
        now = timezone.now()
        with transaction.atomic():
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "data.accessed",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        evaluation_id=evaluation.id,
                        payload={"evaluation_id": str(evaluation.id), "fields": ["model_calls"]},
                    )
                ],
            )
        return Response(
            {
                "results": [
                    {
                        "id": str(c.id),
                        "status": c.status,
                        "superseded": c.superseded,
                        "purpose": c.purpose,
                        "sample_index": c.sample_index,
                        "attempt": c.attempt,
                        "provider": c.provider,
                        "model_requested": c.model_requested,
                        "model_reported": c.model_reported,
                        "valid": c.valid,
                        "error_codes": c.error_codes,
                        "stop_reason": c.stop_reason,
                        "provider_error_code": c.provider_error_code,
                        "input_tokens": c.input_tokens,
                        "output_tokens": c.output_tokens,
                        "latency_ms": c.latency_ms,
                        "created_at": iso_micros(c.created_at),
                        "completed_at": iso_micros(c.completed_at) if c.completed_at else None,
                    }
                    for c in calls
                ]
            }
        )


def _resolve_evaluation(ctx, eval_id) -> Evaluation:
    evaluation = (
        Evaluation.objects.select_related("decision", "decision_version")
        .filter(workspace=ctx.workspace, id=eval_id)
        .first()
    )
    if evaluation is None or not ctx.can_see_decision(evaluation.decision_id):
        raise not_found("evaluation")
    return evaluation


class CancelView(AdjudikAPIView):
    """Cancel an evaluation (SPEC §11.2): the creating ``evaluate`` key or an admin."""

    def post(self, request, eval_id):
        permissions.require(self.ctx, "read")  # narrowed to creator/admin below
        evaluation = _resolve_evaluation(self.ctx, eval_id)
        if not self._may_cancel(evaluation):
            raise forbidden("only the creating key or an admin may cancel")
        data = request.data if isinstance(request.data, dict) else {}
        reason = data.get("reason")
        valid = {c.value for c in CancelReason}
        if reason not in valid:
            raise unprocessable("input_invalid", f"reason must be one of {sorted(valid)}")
        evaluation = service.cancel(
            evaluation.id,
            reason=reason,
            ctx_actor=(self.ctx.actor_type, self.ctx.actor_id),
            now=timezone.now(),
        )
        return Response(service.response_body(evaluation))

    def _may_cancel(self, evaluation) -> bool:
        if self.ctx.actor_type == "user":
            return self.ctx.role == "admin"
        return str(evaluation.created_by_api_key_id) == str(self.ctx.api_key.id)


class LegalHoldView(AdjudikAPIView):
    """Set or clear an evaluation's legal hold (SPEC §9.7, admin)."""

    def post(self, request, eval_id):
        permissions.require(self.ctx, "admin")
        evaluation = _resolve_evaluation(self.ctx, eval_id)
        data = request.data if isinstance(request.data, dict) else {}
        hold = bool(data.get("legal_hold", True))
        now = timezone.now()
        from django.db import transaction

        with transaction.atomic():
            evaluation.legal_hold = hold
            evaluation.updated_at = now
            evaluation.save(update_fields=["legal_hold", "updated_at"])
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "evaluation.legal_hold_changed",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        evaluation_id=evaluation.id,
                        payload={"evaluation_id": str(evaluation.id), "legal_hold": hold},
                    )
                ],
            )
        return Response({"id": str(evaluation.id), "legal_hold": evaluation.legal_hold})


class PurgeView(AdjudikAPIView):
    """Immediately purge an evaluation's data (SPEC §9.7, admin)."""

    def post(self, request, eval_id):
        permissions.require(self.ctx, "admin")
        evaluation = _resolve_evaluation(self.ctx, eval_id)
        data = request.data if isinstance(request.data, dict) else {}
        scope = data.get("scope")
        reason = data.get("reason")
        ticket_ref = data.get("ticket_ref")
        if scope not in ("input", "all"):
            raise unprocessable("input_invalid", "scope must be input or all")
        if reason not in ("erasure_request", "data_error", "other"):
            raise unprocessable(
                "input_invalid", "reason must be erasure_request, data_error or other"
            )
        if evaluation.status not in (EvaluationStatus.DECIDED, EvaluationStatus.CANCELLED):
            raise conflict("invalid_state", "only a decided or cancelled case can be purged")
        if evaluation.legal_hold or evaluation.decision.legal_hold:
            raise conflict("invalid_state", "the case is under legal hold")
        retention.purge_one(
            evaluation,
            scope=scope,
            reason=reason,
            ticket_ref=ticket_ref,
            actor=(self.ctx.actor_type, self.ctx.actor_id),
            now=timezone.now(),
        )
        evaluation.refresh_from_db()
        return Response(
            {
                "id": str(evaluation.id),
                "input_purged_at": iso_micros(evaluation.input_purged_at)
                if evaluation.input_purged_at
                else None,
                "explanation_purged_at": iso_micros(evaluation.explanation_purged_at)
                if evaluation.explanation_purged_at
                else None,
            }
        )
