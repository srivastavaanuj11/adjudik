"""Review task model (SPEC §10.1, §8.4-§8.6).

At most one gate and one audit task per evaluation; at most one claimed task per
reviewer. Completed decision fields are frozen by a trigger (SPEC §9.6).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.accounts.models import Workspace
from apps.decisions.models import Decision
from apps.evaluations.models import Evaluation


class ReviewKind(models.TextChoices):
    GATE = "gate"
    AUDIT = "audit"


class ReviewStatus(models.TextChoices):
    OPEN = "open"
    CLAIMED = "claimed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ReviewAction(models.TextChoices):
    ACCEPT = "accept"
    OVERRIDE = "override"
    DECIDE = "decide"


class ClaimVia(models.TextChoices):
    NEXT = "next"
    DIRECT = "direct"


class ReviewTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="review_tasks")
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name="review_tasks")
    evaluation = models.ForeignKey(
        Evaluation, on_delete=models.CASCADE, related_name="review_tasks"
    )
    kind = models.CharField(max_length=8, choices=ReviewKind.choices)
    priority = models.IntegerField()  # 0 gate, 1 audit
    reason = models.CharField(max_length=32)
    status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.OPEN
    )
    suggested_outcome = models.CharField(max_length=64, null=True, blank=True)
    suggested_confidence = models.FloatField(null=True, blank=True)
    blind = models.BooleanField(default=False)
    label_propensity = models.FloatField(null=True, blank=True)
    due_at = models.DateTimeField()
    claimed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="claimed_reviews",
    )
    claimed_at = models.DateTimeField(null=True, blank=True)
    claim_via = models.CharField(max_length=8, choices=ClaimVia.choices, null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="completed_reviews",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    action = models.CharField(max_length=8, choices=ReviewAction.choices, null=True, blank=True)
    final_outcome = models.CharField(max_length=64, null=True, blank=True)
    agrees_with_model = models.BooleanField(null=True, blank=True)
    reason_codes = models.JSONField(default=list)
    reason_text = models.TextField(null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "review_task"
        constraints = [
            models.UniqueConstraint(fields=["workspace", "id"], name="review_task_workspace_id"),
            models.UniqueConstraint(
                fields=["evaluation", "kind"], name="review_task_unique_eval_kind"
            ),
            models.UniqueConstraint(
                fields=["workspace", "claimed_by"],
                condition=models.Q(status="claimed"),
                name="review_task_one_claim_per_reviewer",
            ),
        ]
        indexes = [
            models.Index(
                fields=["workspace", "priority", "due_at", "created_at"],
                condition=models.Q(status__in=["open", "claimed"]),
                name="review_queue_ws",
            ),
            models.Index(
                fields=["workspace", "decision", "priority", "due_at", "created_at"],
                condition=models.Q(status__in=["open", "claimed"]),
                name="review_queue_ws_decision",
            ),
        ]
