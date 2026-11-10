"""Workspace, settings, membership and API-key models (SPEC §10.1, §15.2).

Every tenant-scoped table carries ``workspace_id``. Parents declare a
``UniqueConstraint(workspace, id)`` so children can hold a database-level
composite foreign key ``(workspace_id, parent_id)`` (added in the grants/triggers
migration), which makes a cross-workspace reference impossible (SPEC §10).
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

SYSTEM_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class Role(models.TextChoices):
    ADMIN = "admin"
    AUTHOR = "author"
    REVIEWER = "reviewer"
    AUDITOR = "auditor"


class ReviewerClaimMode(models.TextChoices):
    ANY = "any"
    NEXT_ONLY = "next_only"


class Workspace(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=64, unique=True)
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "workspace"

    def __str__(self) -> str:  # pragma: no cover - debug aid
        return self.slug


class WorkspaceSettings(models.Model):
    """Per-workspace settings (SPEC §8.1) plus the encrypted HMAC key (§9.2)."""

    workspace = models.OneToOneField(
        Workspace, on_delete=models.CASCADE, primary_key=True, related_name="settings"
    )
    min_threshold = models.FloatField(default=0.80)
    min_audit_sample_rate = models.FloatField(default=0.02)
    calibration_blind_rate = models.FloatField(default=0.25)
    require_four_eyes = models.BooleanField(default=False)
    reviewer_claim_mode = models.CharField(
        max_length=16, choices=ReviewerClaimMode.choices, default=ReviewerClaimMode.ANY
    )
    daily_token_budget = models.BigIntegerField(null=True, blank=True)
    breaker_window = models.IntegerField(default=50)
    breaker_min_audits = models.IntegerField(default=30)
    hmac_key_encrypted = models.TextField()
    updated_by = models.CharField(max_length=150, null=True, blank=True)
    updated_at = models.DateTimeField()

    class Meta:
        db_table = "workspace_settings"


class Membership(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="memberships"
    )
    role = models.CharField(max_length=16, choices=Role.choices)
    created_at = models.DateTimeField()

    class Meta:
        db_table = "membership"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace", "user"], name="membership_unique_workspace_user"
            ),
            models.UniqueConstraint(fields=["workspace", "id"], name="membership_workspace_id"),
        ]


class ApiKeyScope(models.TextChoices):
    EVALUATE = "evaluate"
    READ = "read"
    READ_SENSITIVE = "read_sensitive"
    MANAGE = "manage"


class ApiKey(models.Model):
    """API key (SPEC §15.2). Only the prefix and the SHA-256 of the secret are stored.

    ``previous_key_hash``/``previous_key_expires_at`` carry a rotated key's prior
    secret so it stays valid for 24 h (SPEC §15.2); §10.1 omitted these columns
    (see ADR 0005).
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="api_keys")
    name = models.CharField(max_length=200)
    prefix = models.CharField(max_length=8, unique=True)
    key_hash = models.CharField(max_length=64)
    previous_key_hash = models.CharField(max_length=64, null=True, blank=True)
    previous_key_expires_at = models.DateTimeField(null=True, blank=True)
    scopes = models.JSONField(default=list)
    decision_ids = models.JSONField(null=True, blank=True)  # null = all decisions
    expires_at = models.DateTimeField()
    created_by = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_key"
        indexes = [models.Index(fields=["workspace", "prefix"])]


class UserPasswordState(models.Model):
    """First-login password-change state (SPEC §15.2); not in §10.1 (see ADR 0005)."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="password_state",
    )
    must_change = models.BooleanField(default=False)
    temp_expires_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "user_password_state"
