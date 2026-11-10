"""Evaluation and model-call models (SPEC §10.1).

An evaluation's decision fields become immutable once it is ``decided`` or
``cancelled`` (trigger, SPEC §9.6). Timestamps are set from the injected clock,
never ``auto_now_add`` (SPEC §16.4).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.accounts.models import Workspace
from apps.decisions.models import Decision, DecisionVersion


class EvaluationStatus(models.TextChoices):
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    DECIDED = "decided"
    CANCELLED = "cancelled"


class DecidedBy(models.TextChoices):
    RULE = "rule"
    MODEL = "model"
    HUMAN = "human"


class CancelReason(models.TextChoices):
    WITHDRAWN_BY_CUSTOMER = "withdrawn_by_customer"
    DUPLICATE = "duplicate"
    CLIENT_ERROR = "client_error"
    OTHER = "other"


class Evaluation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="evaluations")
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name="evaluations")
    decision_version = models.ForeignKey(
        DecisionVersion, on_delete=models.PROTECT, related_name="evaluations"
    )
    external_id = models.CharField(max_length=200, null=True, blank=True)
    idempotency_key = models.CharField(max_length=200, null=True, blank=True)
    request_hmac = models.CharField(max_length=64, null=True, blank=True)
    subject_hmac = models.CharField(max_length=64, null=True, blank=True)
    salt = models.BinaryField(null=True, blank=True)

    input = models.JSONField(null=True, blank=True)
    input_hash = models.CharField(max_length=64, null=True, blank=True)
    minimized_input = models.JSONField(null=True, blank=True)
    minimized_input_hash = models.CharField(max_length=64, null=True, blank=True)

    status = models.CharField(max_length=16, choices=EvaluationStatus.choices)
    state_version = models.IntegerField(default=1)
    outcome = models.CharField(max_length=64, null=True, blank=True)
    decided_by = models.CharField(max_length=8, choices=DecidedBy.choices, null=True, blank=True)
    route_reason = models.CharField(max_length=32, null=True, blank=True)

    rule_result = models.JSONField(null=True, blank=True)
    rule_trace = models.JSONField(null=True, blank=True)
    injection_flags = models.JSONField(null=True, blank=True)
    handed_to_model = models.BooleanField(default=False)

    suggested_outcome = models.CharField(max_length=64, null=True, blank=True)
    raw_confidence = models.FloatField(null=True, blank=True)
    calibrated_confidence = models.FloatField(null=True, blank=True)
    threshold = models.FloatField(null=True, blank=True)
    automation_policy_id = models.UUIDField(null=True, blank=True)
    model_reported = models.CharField(max_length=128, null=True, blank=True)
    model_rationale = models.TextField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True)

    model_attempts = models.IntegerField(default=0)
    model_enqueued_at = models.DateTimeField(null=True, blank=True)
    model_started_at = models.DateTimeField(null=True, blank=True)

    legal_hold = models.BooleanField(default=False)
    cancel_reason = models.CharField(max_length=32, null=True, blank=True)
    created_by_api_key_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    decided_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    input_purged_at = models.DateTimeField(null=True, blank=True)
    explanation_purged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "evaluation"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "id"], name="evaluation_workspace_id"),
            models.UniqueConstraint(
                fields=["workspace", "decision", "idempotency_key"],
                condition=models.Q(idempotency_key__isnull=False),
                name="evaluation_unique_idempotency",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace", "decision", "-created_at"], name="eval_ws_decision_created"
            ),
            models.Index(fields=["workspace", "external_id"], name="eval_ws_external"),
            models.Index(
                fields=["workspace", "decision", "subject_hmac", "created_at"],
                name="eval_ws_subject",
            ),
            models.Index(
                fields=["model_enqueued_at"],
                condition=models.Q(status="processing"),
                name="eval_processing_enqueued",
            ),
            models.Index(
                fields=["workspace", "status", "created_at"],
                condition=models.Q(status__in=["processing", "pending_review"]),
                name="eval_ws_open_status",
            ),
        ]


class ModelCallStatus(models.TextChoices):
    STARTED = "started"
    COMPLETED = "completed"
    LOST = "lost"


class ModelCall(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="model_calls")
    evaluation = models.ForeignKey(Evaluation, on_delete=models.CASCADE, related_name="model_calls")
    status = models.CharField(max_length=16, choices=ModelCallStatus.choices)
    superseded = models.BooleanField(default=False)
    purpose = models.CharField(max_length=32)
    sample_index = models.IntegerField(default=0)
    attempt = models.IntegerField(default=1)
    provider = models.CharField(max_length=64)
    model_requested = models.CharField(max_length=128)
    model_reported = models.CharField(max_length=128, null=True, blank=True)
    request_hash = models.CharField(max_length=64)
    system = models.TextField(null=True, blank=True)
    user = models.TextField(null=True, blank=True)
    response_text = models.TextField(null=True, blank=True)
    parsed_output = models.JSONField(null=True, blank=True)
    valid = models.BooleanField(null=True, blank=True)
    error_codes = models.JSONField(default=list)
    stop_reason = models.CharField(max_length=32, null=True, blank=True)
    provider_error_code = models.CharField(max_length=32, null=True, blank=True)
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    created_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "model_call"
        indexes = [
            models.Index(fields=["evaluation", "created_at"], name="model_call_eval_created")
        ]
