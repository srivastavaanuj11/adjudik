"""Audit log, chain head, checkpoint and verification models (SPEC §9, §10.1).

``audit_event`` has no foreign keys (appending takes no locks on other tables)
and is append-only: ``adjudik_app`` holds only SELECT/INSERT and a BEFORE UPDATE
OR DELETE trigger raises for every role (both set up in the grants/triggers
migration). ``id`` is a bigint identity, not a UUID.
"""

from __future__ import annotations

import uuid

from django.db import models


class ActorType(models.TextChoices):
    SYSTEM = "system"
    USER = "user"
    API_KEY = "api_key"


class AuditEvent(models.Model):
    id = models.BigAutoField(primary_key=True)
    workspace_id = models.UUIDField()  # no FK by design (SPEC §9.1)
    seq = models.BigIntegerField()
    event_type = models.CharField(max_length=64)
    occurred_at = models.DateTimeField()
    actor_type = models.CharField(max_length=8, choices=ActorType.choices)
    actor_id = models.CharField(max_length=150, null=True, blank=True)
    evaluation_id = models.UUIDField(null=True, blank=True)
    subject_type = models.CharField(max_length=64, null=True, blank=True)
    subject_id = models.CharField(max_length=64, null=True, blank=True)
    payload = models.JSONField()
    prev_hash = models.CharField(max_length=64)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "audit_event"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace_id", "seq"], name="audit_event_unique_ws_seq"
            ),
        ]
        indexes = [
            models.Index(fields=["workspace_id", "evaluation_id", "seq"], name="audit_event_eval"),
            models.Index(
                fields=["workspace_id", "event_type", "occurred_at"], name="audit_event_type_time"
            ),
        ]


class AuditChainHead(models.Model):
    # Plain UUID (no FK): the system chain uses the all-zeros id, which has no
    # workspace row (SPEC §9.1).
    workspace_id = models.UUIDField(primary_key=True)
    seq = models.BigIntegerField(default=0)
    hash = models.CharField(max_length=64)

    class Meta:
        db_table = "audit_chain_head"


class AuditCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace_id = models.UUIDField()
    seq = models.BigIntegerField()
    hash = models.CharField(max_length=64)
    created_at = models.DateTimeField()
    key_id = models.CharField(max_length=16)
    signature = models.CharField(max_length=128)
    anchored_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "audit_checkpoint"
        constraints = [
            models.UniqueConstraint(
                fields=["workspace_id", "seq"], name="audit_checkpoint_unique_ws_seq"
            ),
        ]


class VerificationMode(models.TextChoices):
    INCREMENTAL = "incremental"
    FULL = "full"
    RECONCILE = "reconcile"


class VerificationStatus(models.TextChoices):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class AuditVerification(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace_id = models.UUIDField()
    mode = models.CharField(max_length=16, choices=VerificationMode.choices)
    status = models.CharField(
        max_length=16, choices=VerificationStatus.choices, default=VerificationStatus.QUEUED
    )
    ok = models.BooleanField(null=True, blank=True)
    from_seq = models.BigIntegerField(null=True, blank=True)
    head_seq = models.BigIntegerField(null=True, blank=True)
    first_bad_seq = models.BigIntegerField(null=True, blank=True)
    mismatches = models.JSONField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    requested_by = models.CharField(max_length=150, null=True, blank=True)
    created_at = models.DateTimeField()
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "audit_verification"
