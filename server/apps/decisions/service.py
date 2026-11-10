"""Decision, version and policy services (SPEC §5.7, §5.8, §8.1, §11.2, §11.8).

Pure parsing/compilation helpers plus the transactional operations (create
draft, replace draft, publish, retire, acknowledge-model, and the manual
policy lifecycle). Each transactional operation appends its audit events as the
last statement before commit (SPEC §9.3).
"""

from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.db.models import Max

from adjudik_core import CompiledDefinition, DefinitionError, load_definition, validate_definition
from adjudik_core.definition.yaml_loader import YAMLLoadError, load_yaml
from apps.common import audit
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.engine import build_engine, provider_names
from apps.common.errors import conflict, unprocessable

from .models import (
    AutomationPolicy,
    DeactivationReason,
    Decision,
    DecisionVersion,
    PolicySource,
    PolicyStatus,
    VersionStatus,
)

# --- parsing and validation (SPEC §11.8) ---


@dataclass
class ParsedDefinition:
    doc: dict[str, Any]
    source_text: str
    source_format: str  # "yaml" | "json"


def parse_body(raw: Any, content_type: str) -> ParsedDefinition:
    """Parse a draft upload: raw YAML text, or JSON ``{"definition": {...}}``."""
    if content_type.startswith("application/yaml"):
        if not isinstance(raw, str):
            raise unprocessable("definition_invalid", "expected a YAML document body")
        try:
            doc = load_yaml(raw)
        except YAMLLoadError as exc:
            raise unprocessable("definition_invalid", "invalid YAML") from exc
        if not isinstance(doc, dict):
            raise unprocessable("definition_invalid", "definition must be a mapping")
        return ParsedDefinition(doc=doc, source_text=raw, source_format="yaml")

    body = raw if isinstance(raw, dict) else {}
    doc = body.get("definition")
    if not isinstance(doc, dict):
        raise unprocessable("definition_invalid", 'body must be {"definition": {...}}')
    source_text = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2)
    return ParsedDefinition(doc=doc, source_format="json", source_text=source_text)


def issue_details(doc: dict[str, Any]) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Return ``(errors, warnings)`` as API ``details`` dicts (SPEC §11.8)."""
    issues = validate_definition(doc)
    errors = [
        {"path": i.path or "/", "code": i.code, "message": i.message} for i in issues if i.is_error
    ]
    warnings = [
        {"path": i.path or "/", "code": i.code, "message": i.message}
        for i in issues
        if not i.is_error
    ]
    return errors, warnings


def compile_or_422(doc: dict[str, Any]) -> CompiledDefinition:
    """Compile a definition, raising ``422 definition_invalid`` with all errors."""
    try:
        return load_definition(doc)
    except DefinitionError as exc:
        details = [
            {"path": i.path or "/", "code": i.code, "message": i.message}
            for i in exc.issues
            if i.is_error
        ]
        raise unprocessable("definition_invalid", "definition has errors", details=details) from exc


# --- decisions and drafts ---


def next_version_number(decision: Decision) -> int:
    current = DecisionVersion.objects.filter(decision=decision).aggregate(m=Max("version"))["m"]
    return (current or 0) + 1


@transaction.atomic
def create_draft(
    *,
    decision: Decision,
    parsed: ParsedDefinition,
    compiled: CompiledDefinition,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> DecisionVersion:
    version = DecisionVersion.objects.create(
        workspace=decision.workspace,
        decision=decision,
        version=next_version_number(decision),
        status=VersionStatus.DRAFT,
        definition=compiled.definition.model_dump(mode="json"),
        source_text=parsed.source_text,
        source_format=parsed.source_format,
        definition_hash=compiled.definition_hash,
        rules_hash=compiled.rules_hash or "",
        created_by=actor_name,
        updated_by=actor_name,
        created_at=now,
        updated_at=now,
    )
    audit.append_many(
        decision.workspace_id,
        [
            Event(
                "decision.draft_saved",
                now,
                actor_type=actor_type,
                actor_id=actor_id,
                subject_type="decision_version",
                subject_id=str(version.id),
                payload={
                    "decision_id": str(decision.id),
                    "version": version.version,
                    "definition_hash": compiled.definition_hash,
                },
            )
        ],
    )
    return version


@transaction.atomic
def replace_draft(
    *,
    version: DecisionVersion,
    parsed: ParsedDefinition,
    compiled: CompiledDefinition,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> DecisionVersion:
    if version.status != VersionStatus.DRAFT:
        raise conflict("version_not_draft", "only a draft may be replaced")
    version.definition = compiled.definition.model_dump(mode="json")
    version.source_text = parsed.source_text
    version.source_format = parsed.source_format
    version.definition_hash = compiled.definition_hash
    version.rules_hash = compiled.rules_hash or ""
    version.updated_by = actor_name
    version.updated_at = now
    version.save(
        update_fields=[
            "definition",
            "source_text",
            "source_format",
            "definition_hash",
            "rules_hash",
            "updated_by",
            "updated_at",
        ]
    )
    audit.append_many(
        version.workspace_id,
        [
            Event(
                "decision.draft_saved",
                now,
                actor_type=actor_type,
                actor_id=actor_id,
                subject_type="decision_version",
                subject_id=str(version.id),
                payload={
                    "decision_id": str(version.decision_id),
                    "version": version.version,
                    "definition_hash": compiled.definition_hash,
                },
            )
        ],
    )
    return version


# --- publish, retire, acknowledge-model (SPEC §11.2) ---


def _check_four_eyes(require_four_eyes: bool, actor_name: str | None, *authors: str | None) -> None:
    """Rule for publish/approve under four-eyes (SPEC §8.1)."""
    if require_four_eyes and actor_name is not None and actor_name in {a for a in authors if a}:
        raise conflict(
            "four_eyes_required",
            "a different admin must publish or approve this",
        )


@transaction.atomic
def publish_version(
    *,
    version: DecisionVersion,
    carry_over_policy: bool,
    require_four_eyes: bool,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> DecisionVersion:
    # Lock the decision so concurrent publishes serialise on the active slot.
    decision = Decision.objects.select_for_update().get(pk=version.decision_id)
    version = DecisionVersion.objects.select_for_update().get(pk=version.pk)
    if version.status != VersionStatus.DRAFT:
        raise conflict("version_not_draft", "only a draft may be published")

    _check_four_eyes(require_four_eyes, actor_name, version.created_by, version.updated_by)

    compiled = load_definition(version.definition)

    fingerprint: str | None = None
    if compiled.definition.model_step is not None:
        provider = compiled.definition.model_step.provider
        if provider not in provider_names():
            raise unprocessable(
                "definition_invalid",
                "provider is not configured",
                details=[
                    {
                        "path": "/model_step/provider",
                        "code": "provider_not_configured",
                        "message": f"provider {provider!r} is not in ADJUDIK_PROVIDERS",
                    }
                ],
            )
        fingerprint = build_engine().fingerprint(compiled)

    previous = (
        DecisionVersion.objects.select_for_update()
        .filter(decision=decision, status=VersionStatus.ACTIVE)
        .first()
    )
    previous_policy: AutomationPolicy | None = None
    previous_version_number: int | None = None
    if previous is not None:
        previous_version_number = previous.version
        previous_policy = AutomationPolicy.objects.filter(
            decision_version=previous, status=PolicyStatus.ACTIVE
        ).first()
        previous.status = VersionStatus.RETIRED
        previous.retired_at = now
        previous.save(update_fields=["status", "retired_at"])
        if previous_policy is not None:
            previous_policy.status = PolicyStatus.DEACTIVATED
            previous_policy.deactivated_at = now
            previous_policy.deactivation_reason = DeactivationReason.RETIRED
            previous_policy.save(update_fields=["status", "deactivated_at", "deactivation_reason"])

    version.status = VersionStatus.ACTIVE
    version.model_step_fingerprint = fingerprint
    version.published_by = actor_name
    version.published_at = now
    version.save(update_fields=["status", "model_step_fingerprint", "published_by", "published_at"])

    # Copy name/description onto the decision (SPEC §10.1).
    decision.name = compiled.definition.name
    decision.description = compiled.definition.description
    decision.save(update_fields=["name", "description"])

    events = [
        Event(
            "decision.version_published",
            now,
            actor_type=actor_type,
            actor_id=actor_id,
            subject_type="decision_version",
            subject_id=str(version.id),
            payload={
                "decision_id": str(decision.id),
                "version": version.version,
                "definition_hash": version.definition_hash,
                "rules_hash": version.rules_hash or None,
                "model_step_fingerprint": fingerprint,
                "previous_active_version": previous_version_number,
            },
        )
    ]

    carried: AutomationPolicy | None = None
    if carry_over_policy and previous_policy is not None:
        carried = AutomationPolicy.objects.create(
            workspace=version.workspace,
            decision_version=version,
            status=PolicyStatus.ACTIVE,
            thresholds=previous_policy.thresholds,
            targets=previous_policy.targets,
            audit_sample_rate=previous_policy.audit_sample_rate,
            calibration_map=previous_policy.calibration_map,
            source=PolicySource.CARRIED_OVER,
            note="carried over on publish",
            proposed_by=actor_name,
            proposed_at=now,
            decided_by_user=actor_name,
            decided_at=now,
            activated_at=now,
        )
        events.append(
            _policy_event("automation_policy.activated", carried, now, actor_type, actor_id)
        )

    audit.append_many(version.workspace_id, events)
    return version


@transaction.atomic
def retire_version(
    *,
    version: DecisionVersion,
    actor_type: str,
    actor_id: str | None,
    now: _dt.datetime,
) -> DecisionVersion:
    version = DecisionVersion.objects.select_for_update().get(pk=version.pk)
    if version.status != VersionStatus.ACTIVE:
        raise conflict("invalid_state", "only an active version may be retired")
    version.status = VersionStatus.RETIRED
    version.retired_at = now
    version.save(update_fields=["status", "retired_at"])

    events = [
        Event(
            "decision.version_retired",
            now,
            actor_type=actor_type,
            actor_id=actor_id,
            subject_type="decision_version",
            subject_id=str(version.id),
            payload={"decision_id": str(version.decision_id), "version": version.version},
        )
    ]
    policy = AutomationPolicy.objects.filter(
        decision_version=version, status=PolicyStatus.ACTIVE
    ).first()
    if policy is not None:
        policy.status = PolicyStatus.DEACTIVATED
        policy.deactivated_at = now
        policy.deactivation_reason = DeactivationReason.RETIRED
        policy.save(update_fields=["status", "deactivated_at", "deactivation_reason"])
        events.append(
            _policy_event("automation_policy.deactivated", policy, now, actor_type, actor_id)
        )

    audit.append_many(version.workspace_id, events)
    return version


@transaction.atomic
def acknowledge_model(
    *,
    version: DecisionVersion,
    actor_type: str,
    actor_id: str | None,
    now: _dt.datetime,
) -> DecisionVersion:
    """Reset the observed model so cases stop routing to review (SPEC §8.2)."""
    version = DecisionVersion.objects.select_for_update().get(pk=version.pk)
    observed = version.observed_model
    version.observed_model = None
    version.save(update_fields=["observed_model"])
    audit.append_many(
        version.workspace_id,
        [
            Event(
                "decision.model_acknowledged",
                now,
                actor_type=actor_type,
                actor_id=actor_id,
                subject_type="decision_version",
                subject_id=str(version.id),
                payload={
                    "decision_id": str(version.decision_id),
                    "version": version.version,
                    "observed_model": observed,
                },
            )
        ],
    )
    return version


# --- manual automation policies (SPEC §8.1, §11.2) ---


def validate_policy_fields(
    *,
    thresholds: Any,
    audit_sample_rate: Any,
    targets: Any,
    outcome_ids: set[str],
    min_threshold: float,
    min_audit_sample_rate: float,
) -> tuple[dict[str, float | None], float, dict[str, float]]:
    """Check a manual policy against workspace floors (SPEC §8.1)."""
    if not isinstance(thresholds, dict):
        raise unprocessable("policy_invalid", "thresholds must be a mapping")
    clean_thresholds: dict[str, float | None] = {}
    has_automation = False
    for outcome, value in thresholds.items():
        if outcome not in outcome_ids:
            raise unprocessable("policy_invalid", "threshold names an unknown outcome")
        if value is None:
            clean_thresholds[outcome] = None
            continue
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            raise unprocessable("policy_invalid", "threshold must be a number or null")
        value = float(value)
        if not (min_threshold <= value <= 1.0):
            raise unprocessable("policy_invalid", f"threshold must be in [{min_threshold}, 1]")
        clean_thresholds[outcome] = value
        has_automation = True

    clean_targets: dict[str, float] = {}
    if targets is not None:
        if not isinstance(targets, dict):
            raise unprocessable("policy_invalid", "targets must be a mapping")
        for outcome, value in targets.items():
            if outcome not in outcome_ids:
                raise unprocessable("policy_invalid", "target names an unknown outcome")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise unprocessable("policy_invalid", "target must be a number")
            value = float(value)
            if not (0.5 <= value <= 0.999):
                raise unprocessable("policy_invalid", "target must be in [0.5, 0.999]")
            clean_targets[outcome] = value

    if has_automation:
        if not isinstance(audit_sample_rate, (int, float)) or isinstance(audit_sample_rate, bool):
            raise unprocessable("policy_invalid", "audit_sample_rate is required with automation")
        rate = float(audit_sample_rate)
        if not (min_audit_sample_rate <= rate <= 0.5):
            raise unprocessable(
                "policy_invalid", f"audit_sample_rate must be in [{min_audit_sample_rate}, 0.5]"
            )
    else:
        rate = float(audit_sample_rate) if isinstance(audit_sample_rate, (int, float)) else 0.0
        if not (0.0 <= rate <= 0.5):
            raise unprocessable("policy_invalid", "audit_sample_rate must be in [0, 0.5]")

    return clean_thresholds, rate, clean_targets


def _policy_event(
    event_type: str,
    policy: AutomationPolicy,
    now: _dt.datetime,
    actor_type: str,
    actor_id: str | None,
    *,
    reason: str | None = None,
) -> Event:
    return Event(
        event_type,
        now,
        actor_type=actor_type,
        actor_id=actor_id,
        subject_type="automation_policy",
        subject_id=str(policy.id),
        payload={
            "policy_id": str(policy.id),
            "decision_version_id": str(policy.decision_version_id),
            "source": policy.source,
            "thresholds": policy.thresholds,
            "targets": policy.targets,
            "audit_sample_rate": policy.audit_sample_rate,
            "calibration_run_id": str(policy.calibration_run_id)
            if policy.calibration_run_id
            else None,
            "reason": reason,
        },
    )


@transaction.atomic
def propose_policy(
    *,
    version: DecisionVersion,
    thresholds: dict[str, float | None],
    audit_sample_rate: float,
    targets: dict[str, float],
    note: str,
    require_four_eyes: bool,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> AutomationPolicy:
    """Propose a manual policy; it activates at once unless four-eyes is on."""
    DecisionVersion.objects.select_for_update().get(pk=version.pk)
    policy = AutomationPolicy(
        workspace=version.workspace,
        decision_version=version,
        thresholds=thresholds,
        targets=targets,
        audit_sample_rate=audit_sample_rate,
        source=PolicySource.MANUAL,
        note=note,
        proposed_by=actor_name,
        proposed_at=now,
    )
    events: list[Event] = []
    if require_four_eyes:
        policy.status = PolicyStatus.PROPOSED
        policy.save()
        events.append(
            _policy_event("automation_policy.proposed", policy, now, actor_type, actor_id)
        )
    else:
        _deactivate_active_policy(version, now, events, actor_type, actor_id)
        policy.status = PolicyStatus.ACTIVE
        policy.decided_by_user = actor_name
        policy.decided_at = now
        policy.activated_at = now
        policy.save()
        events.append(
            _policy_event("automation_policy.activated", policy, now, actor_type, actor_id)
        )
    audit.append_many(version.workspace_id, events)
    return policy


def _deactivate_active_policy(
    version: DecisionVersion,
    now: _dt.datetime,
    events: list[Event],
    actor_type: str,
    actor_id: str | None,
) -> None:
    active = (
        AutomationPolicy.objects.select_for_update()
        .filter(decision_version=version, status=PolicyStatus.ACTIVE)
        .first()
    )
    if active is not None:
        active.status = PolicyStatus.DEACTIVATED
        active.deactivated_at = now
        active.deactivation_reason = DeactivationReason.REPLACED
        active.save(update_fields=["status", "deactivated_at", "deactivation_reason"])
        events.append(
            _policy_event(
                "automation_policy.deactivated",
                active,
                now,
                actor_type,
                actor_id,
                reason="replaced",
            )
        )


@transaction.atomic
def approve_policy(
    *,
    policy: AutomationPolicy,
    require_four_eyes: bool,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> AutomationPolicy:
    policy = AutomationPolicy.objects.select_for_update().get(pk=policy.pk)
    if policy.status != PolicyStatus.PROPOSED:
        raise conflict("invalid_state", "only a proposed policy may be approved")
    _check_four_eyes(require_four_eyes, actor_name, policy.proposed_by)
    version = DecisionVersion.objects.select_for_update().get(pk=policy.decision_version_id)
    events: list[Event] = []
    _deactivate_active_policy(version, now, events, actor_type, actor_id)
    policy.status = PolicyStatus.ACTIVE
    policy.decided_by_user = actor_name
    policy.decided_at = now
    policy.activated_at = now
    policy.save(update_fields=["status", "decided_by_user", "decided_at", "activated_at"])
    events.append(_policy_event("automation_policy.activated", policy, now, actor_type, actor_id))
    audit.append_many(policy.workspace_id, events)
    return policy


@transaction.atomic
def reject_policy(
    *,
    policy: AutomationPolicy,
    actor_type: str,
    actor_id: str | None,
    actor_name: str | None,
    now: _dt.datetime,
) -> AutomationPolicy:
    policy = AutomationPolicy.objects.select_for_update().get(pk=policy.pk)
    if policy.status != PolicyStatus.PROPOSED:
        raise conflict("invalid_state", "only a proposed policy may be rejected")
    policy.status = PolicyStatus.REJECTED
    policy.decided_by_user = actor_name
    policy.decided_at = now
    policy.save(update_fields=["status", "decided_by_user", "decided_at"])
    audit.append_many(
        policy.workspace_id,
        [_policy_event("automation_policy.rejected", policy, now, actor_type, actor_id)],
    )
    return policy


@transaction.atomic
def deactivate_policy(
    *,
    policy: AutomationPolicy,
    actor_type: str,
    actor_id: str | None,
    now: _dt.datetime,
) -> AutomationPolicy:
    policy = AutomationPolicy.objects.select_for_update().get(pk=policy.pk)
    if policy.status != PolicyStatus.ACTIVE:
        raise conflict("invalid_state", "only an active policy may be deactivated")
    policy.status = PolicyStatus.DEACTIVATED
    policy.deactivated_at = now
    policy.deactivation_reason = DeactivationReason.MANUAL
    policy.save(update_fields=["status", "deactivated_at", "deactivation_reason"])
    audit.append_many(
        policy.workspace_id,
        [
            _policy_event(
                "automation_policy.deactivated", policy, now, actor_type, actor_id, reason="manual"
            )
        ],
    )
    return policy


# --- serialisation ---


def version_summary(v: DecisionVersion) -> dict[str, Any]:
    return {
        "version": v.version,
        "status": v.status,
        "definition_hash": v.definition_hash,
        "rules_hash": v.rules_hash or None,
        "model_step_fingerprint": v.model_step_fingerprint,
        "created_at": iso_micros(v.created_at),
        "published_at": iso_micros(v.published_at) if v.published_at else None,
        "retired_at": iso_micros(v.retired_at) if v.retired_at else None,
    }


def version_detail(v: DecisionVersion) -> dict[str, Any]:
    body = version_summary(v)
    body.update(
        {
            "decision": v.decision.key,
            "definition": v.definition,
            "source_text": v.source_text,
            "source_format": v.source_format,
            "observed_model": v.observed_model,
            "created_by": v.created_by,
            "updated_by": v.updated_by,
            "published_by": v.published_by,
            "updated_at": iso_micros(v.updated_at),
        }
    )
    return body


def policy_detail(p: AutomationPolicy) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "decision_version_id": str(p.decision_version_id),
        "status": p.status,
        "thresholds": p.thresholds,
        "targets": p.targets,
        "audit_sample_rate": p.audit_sample_rate,
        "calibration_map": p.calibration_map,
        "source": p.source,
        "note": p.note,
        "proposed_by": p.proposed_by,
        "proposed_at": iso_micros(p.proposed_at),
        "decided_by_user": p.decided_by_user,
        "decided_at": iso_micros(p.decided_at) if p.decided_at else None,
        "activated_at": iso_micros(p.activated_at) if p.activated_at else None,
        "deactivated_at": iso_micros(p.deactivated_at) if p.deactivated_at else None,
        "deactivation_reason": p.deactivation_reason,
    }
