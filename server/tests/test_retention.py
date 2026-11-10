"""Retention, purge, legal hold and salt destruction (SPEC §9.7, §17 M3b).

Also checks that the hash chain still verifies and ``reconcile`` still passes
after a purge, since purging never touches outcomes, hashes or audit events.
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import yaml
from rest_framework.test import APIClient

from adjudik_core import load_definition
from apps.accounts.models import Role
from apps.audit import service as audit_service
from apps.audit.models import AuditChainHead, AuditEvent
from apps.decisions.models import Decision, DecisionVersion, VersionStatus
from apps.evaluations import retention
from apps.evaluations.models import (
    DecidedBy,
    Evaluation,
    EvaluationStatus,
    ModelCall,
    ModelCallStatus,
)
from apps.reviews.models import ReviewAction, ReviewKind, ReviewStatus, ReviewTask

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _active_version(workspace, now):
    d = yaml.safe_load((EXAMPLES / "support_routing" / "decision.yaml").read_text())
    d["model_step"]["provider"] = "demo"
    d["model_step"]["model"] = "demo-1"
    compiled = load_definition(d)
    decision = Decision.objects.create(
        workspace=workspace, key=compiled.key, name=compiled.definition.name, created_at=now
    )
    version = DecisionVersion.objects.create(
        workspace=workspace,
        decision=decision,
        version=1,
        status=VersionStatus.ACTIVE,
        definition=compiled.definition.model_dump(mode="json"),
        source_text="",
        source_format="json",
        definition_hash=compiled.definition_hash,
        rules_hash=compiled.rules_hash or "",
        model_step_fingerprint="fp",
        created_at=now,
        updated_at=now,
        published_at=now,
    )
    return decision, version


_SEQ = iter(range(1, 10_000))


def _decided(ws, dec, ver, now, *, decided_at=None, with_review=True):
    from apps.common.hashing import content_hash

    tag = next(_SEQ)
    salt = b"S" * 32
    data = {
        "ticket": {
            "id": "T",
            "subject": "s",
            "body": "secret body",
            "form": "contact",
            "plan": "free",
        }
    }
    ev = Evaluation.objects.create(
        workspace=ws,
        decision=dec,
        decision_version=ver,
        external_id=f"ext-{tag}",
        idempotency_key=f"ik-{tag}",
        salt=salt,
        input=data,
        input_hash=content_hash(salt, data),
        minimized_input=data,
        minimized_input_hash=content_hash(salt, data),
        rule_trace={"t": 1},
        metadata={"channel": "web"},
        injection_flags=None,
        status=EvaluationStatus.DECIDED,
        state_version=2,
        outcome="billing",
        decided_by=DecidedBy.MODEL,
        suggested_outcome="billing",
        raw_confidence=0.95,
        handed_to_model=True,
        model_rationale="because secret",
        created_at=now,
        updated_at=now,
        decided_at=decided_at or now,
    )
    ModelCall.objects.create(
        workspace=ws,
        evaluation=ev,
        status=ModelCallStatus.COMPLETED,
        purpose="primary",
        provider="demo",
        model_requested="demo-1",
        request_hash="h",
        system="sys",
        user="usr",
        response_text="resp",
        parsed_output={"outcome": "billing"},
        created_at=now,
    )
    if with_review:
        ReviewTask.objects.create(
            workspace=ws,
            decision=dec,
            evaluation=ev,
            kind=ReviewKind.GATE,
            priority=0,
            reason="low_confidence",
            status=ReviewStatus.COMPLETED,
            action=ReviewAction.ACCEPT,
            final_outcome="billing",
            reason_text="looks right",
            notes="n",
            due_at=now,
            created_at=now,
            completed_at=now,
        )
    return ev


def _admin(workspace, make_member, username):
    user = make_member(workspace, Role.ADMIN, username=username)
    client = APIClient()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_purge_input_scope(api_client, workspace, make_member, now):
    dec, ver = _active_version(workspace, now)
    ev = _decided(workspace, dec, ver, now)
    client = _admin(workspace, make_member, "pg-1")
    r = client.post(
        f"/api/v1/evaluations/{ev.id}/purge",
        {"scope": "input", "reason": "erasure_request", "ticket_ref": "JIRA-1"},
        format="json",
    )
    assert r.status_code == 200, r.content
    ev.refresh_from_db()
    assert ev.input is None and ev.minimized_input is None and ev.salt is None
    assert ev.external_id is None and ev.idempotency_key is None and ev.rule_trace is None
    assert ev.input_purged_at is not None and ev.explanation_purged_at is None
    # Outcome, score and rationale survive an input-only purge.
    assert ev.outcome == "billing" and ev.raw_confidence == 0.95
    assert ev.model_rationale == "because secret"
    call = ModelCall.objects.get(evaluation=ev)
    assert call.system is None and call.user is None and call.response_text is None
    assert AuditEvent.objects.filter(
        workspace_id=workspace.id, event_type="evaluation.data_purged"
    ).exists()


@pytest.mark.django_db
def test_purge_all_scope_clears_explanation(api_client, workspace, make_member, now):
    dec, ver = _active_version(workspace, now)
    ev = _decided(workspace, dec, ver, now)
    client = _admin(workspace, make_member, "pg-2")
    r = client.post(
        f"/api/v1/evaluations/{ev.id}/purge",
        {"scope": "all", "reason": "data_error"},
        format="json",
    )
    assert r.status_code == 200
    ev.refresh_from_db()
    assert ev.model_rationale is None and ev.explanation_purged_at is not None
    task = ReviewTask.objects.get(evaluation=ev)
    assert task.reason_text is None and task.notes is None


@pytest.mark.django_db
def test_purge_requires_terminal_and_respects_legal_hold(api_client, workspace, make_member, now):
    dec, ver = _active_version(workspace, now)
    client = _admin(workspace, make_member, "pg-3")

    pending = Evaluation.objects.create(
        workspace=workspace,
        decision=dec,
        decision_version=ver,
        salt=b"S" * 32,
        input={"a": 1},
        status=EvaluationStatus.PENDING_REVIEW,
        state_version=1,
        handed_to_model=True,
        created_at=now,
        updated_at=now,
    )
    not_terminal = client.post(
        f"/api/v1/evaluations/{pending.id}/purge",
        {"scope": "input", "reason": "other"},
        format="json",
    )
    assert not_terminal.status_code == 409

    held = _decided(workspace, dec, ver, now)
    held.legal_hold = True
    held.save(update_fields=["legal_hold"])
    r = client.post(
        f"/api/v1/evaluations/{held.id}/purge", {"scope": "input", "reason": "other"}, format="json"
    )
    assert r.status_code == 409


@pytest.mark.django_db
def test_decision_legal_hold_blocks_purge(api_client, workspace, make_member, now):
    dec, ver = _active_version(workspace, now)
    ev = _decided(workspace, dec, ver, now)
    client = _admin(workspace, make_member, "pg-4")
    hold = client.post(
        "/api/v1/decisions/support_routing/legal-hold", {"legal_hold": True}, format="json"
    )
    assert hold.status_code == 200
    r = client.post(
        f"/api/v1/evaluations/{ev.id}/purge", {"scope": "input", "reason": "other"}, format="json"
    )
    assert r.status_code == 409


@pytest.mark.django_db
def test_daily_purge_job_respects_window_and_hold(workspace, now):
    dec, ver = _active_version(workspace, now)
    old = now - _dt.timedelta(days=400)  # older than the 365-day input window
    stale = _decided(workspace, dec, ver, now, decided_at=old)
    held = _decided(workspace, dec, ver, now, decided_at=old)
    held.legal_hold = True
    held.save(update_fields=["legal_hold"])
    fresh = _decided(workspace, dec, ver, now, decided_at=now)

    result = retention.purge_expired_data(now=now)
    assert result["input_purged"] == 1
    stale.refresh_from_db()
    held.refresh_from_db()
    fresh.refresh_from_db()
    assert stale.input is None and stale.input_purged_at is not None
    assert held.input is not None  # legal hold skipped
    assert fresh.input is not None  # inside the retention window


@pytest.mark.django_db
def test_chain_verifies_and_reconciles_after_purge(workspace, make_member, now):
    dec, ver = _active_version(workspace, now)
    ev = _decided(workspace, dec, ver, now)
    # Seed a couple of real audit events so the chain is non-trivial.
    from django.db import transaction

    from apps.common import audit
    from apps.common.audit import Event

    with transaction.atomic():
        audit.append_many(
            workspace.id,
            [Event("evaluation.decided", now, evaluation_id=ev.id, payload={"outcome": "billing"})],
        )

    retention.purge_one(
        ev, scope="all", reason="erasure_request", ticket_ref="X", actor=("system", None), now=now
    )

    head = AuditChainHead.objects.get(workspace_id=workspace.id)
    assert head.seq >= 2
    v = audit_service.run_verification(workspace.id, "reconcile", requested_by=None, now=now)
    assert v.ok is True and v.first_bad_seq is None
