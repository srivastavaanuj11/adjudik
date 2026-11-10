"""Webhook endpoint and delivery (outbox) models (SPEC §10.1, §11.6).

Delivery rows are inserted in the same transaction as the state change they
describe (the outbox). Payloads are thin and never carry inputs, rationale or
metadata (SPEC §11.6).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.accounts.models import Workspace
from apps.decisions.models import Decision


class WebhookEventType(models.TextChoices):
    EVALUATION_DECIDED = "evaluation.decided"
    EVALUATION_PENDING_REVIEW = "evaluation.pending_review"
    EVALUATION_AUDIT_DISAGREEMENT = "evaluation.audit_disagreement"
    EVALUATION_CANCELLED = "evaluation.cancelled"
    AUTOMATION_SUSPENDED = "automation.suspended"


class WebhookEndpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="webhooks")
    decision = models.ForeignKey(
        Decision, on_delete=models.CASCADE, null=True, blank=True, related_name="webhooks"
    )
    url = models.URLField(max_length=2048)
    secret_encrypted = models.TextField()
    previous_secret_encrypted = models.TextField(null=True, blank=True)
    previous_secret_expires_at = models.DateTimeField(null=True, blank=True)
    event_types = models.JSONField(default=list)
    active = models.BooleanField(default=True)
    created_by = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "webhook_endpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "id"], name="webhook_endpoint_workspace_id"
            ),
        ]


class DeliveryStatus(models.TextChoices):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD = "dead"


class WebhookDelivery(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="webhook_deliveries"
    )
    endpoint = models.ForeignKey(
        WebhookEndpoint, on_delete=models.CASCADE, related_name="deliveries"
    )
    event_id = models.UUIDField()
    event_type = models.CharField(max_length=40)
    evaluation_id = models.UUIDField(null=True, blank=True)
    payload = models.JSONField()
    attempt = models.IntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=DeliveryStatus.choices, default=DeliveryStatus.PENDING
    )
    last_status_code = models.IntegerField(null=True, blank=True)
    last_error_class = models.CharField(max_length=32, null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "webhook_delivery"
        constraints = [
            models.UniqueConstraint(
                fields=["endpoint", "event_id"], name="webhook_delivery_unique_endpoint_event"
            ),
        ]
        indexes = [
            models.Index(
                fields=["next_attempt_at"],
                condition=models.Q(status__in=["pending", "failed"]),
                name="webhook_delivery_due",
            ),
        ]
