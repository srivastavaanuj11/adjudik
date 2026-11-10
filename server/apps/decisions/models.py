"""Decision, version and automation-policy models (SPEC §10.1).

Composite ``(workspace_id, id)`` uniqueness on parents backs the DB-level
composite FKs on children (added in the grants/triggers migration). Partial
unique indexes enforce "at most one active version per decision" and "at most
one active policy per version". Immutability of published definitions and
terminal evaluations is enforced by triggers (SPEC §9.6).
"""

from __future__ import annotations

import uuid

from django.db import models

from apps.accounts.models import Workspace


class VersionStatus(models.TextChoices):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"


class SourceFormat(models.TextChoices):
    YAML = "yaml"
    JSON = "json"


class PolicyStatus(models.TextChoices):
    PROPOSED = "proposed"
    ACTIVE = "active"
    REJECTED = "rejected"
    DEACTIVATED = "deactivated"


class PolicySource(models.TextChoices):
    MANUAL = "manual"
    CALIBRATION = "calibration"
    CARRIED_OVER = "carried_over"
    SUSPENSION = "suspension"


class DeactivationReason(models.TextChoices):
    REPLACED = "replaced"
    RETIRED = "retired"
    SUSPENDED = "suspended"
    MANUAL = "manual"


class Decision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="decisions")
    key = models.CharField(max_length=64)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    legal_hold = models.BooleanField(default=False)
    created_by = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "decision"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "key"], name="decision_unique_workspace_key"
            ),
            models.UniqueConstraint(fields=["workspace", "id"], name="decision_workspace_id"),
        ]
        indexes = [models.Index(fields=["workspace", "created_at"])]


class DecisionVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="versions")
    decision = models.ForeignKey(Decision, on_delete=models.CASCADE, related_name="versions")
    version = models.IntegerField()
    status = models.CharField(
        max_length=16, choices=VersionStatus.choices, default=VersionStatus.DRAFT
    )
    definition = models.JSONField()
    source_text = models.TextField(blank=True, default="")
    source_format = models.CharField(max_length=8, choices=SourceFormat.choices)
    definition_hash = models.CharField(max_length=64)
    rules_hash = models.CharField(max_length=64)
    model_step_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    observed_model = models.CharField(max_length=128, null=True, blank=True)
    created_by = models.CharField(max_length=150, null=True, blank=True)
    updated_by = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField()
    updated_at = models.DateTimeField()
    published_by = models.CharField(max_length=150, null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    retired_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "decision_version"
        constraints = [
            models.UniqueConstraint(
                fields=["decision", "version"], name="decision_version_unique_decision_version"
            ),
            models.UniqueConstraint(
                fields=["workspace", "id"], name="decision_version_workspace_id"
            ),
            models.UniqueConstraint(
                fields=["decision"],
                condition=models.Q(status="active"),
                name="decision_version_one_active",
            ),
        ]
        indexes = [models.Index(fields=["model_step_fingerprint"])]


class AutomationPolicy(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="policies")
    decision_version = models.ForeignKey(
        DecisionVersion, on_delete=models.CASCADE, related_name="policies"
    )
    status = models.CharField(max_length=16, choices=PolicyStatus.choices)
    thresholds = models.JSONField(default=dict)
    targets = models.JSONField(default=dict)
    audit_sample_rate = models.FloatField()
    calibration_map = models.JSONField(null=True, blank=True)
    source = models.CharField(max_length=16, choices=PolicySource.choices)
    calibration_run_id = models.UUIDField(null=True, blank=True)
    note = models.CharField(max_length=500, blank=True, default="")
    proposed_by = models.CharField(max_length=150, null=True, blank=True)
    proposed_at = models.DateTimeField()
    decided_by_user = models.CharField(max_length=150, null=True, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    deactivation_reason = models.CharField(
        max_length=16, choices=DeactivationReason.choices, null=True, blank=True
    )

    class Meta:
        db_table = "automation_policy"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "id"], name="automation_policy_workspace_id"
            ),
            models.UniqueConstraint(
                fields=["decision_version"],
                condition=models.Q(status="active"),
                name="automation_policy_one_active",
            ),
        ]
