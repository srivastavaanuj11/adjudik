"""Review queue endpoints (SPEC §8.5, §8.6, §17 M3b).

Covers next/claim/heartbeat/release/complete, the parallel ``next`` and lease
expiry, one-claim-per-reviewer, reason codes, claim modes, blind redaction and
the audit-disagreement path.
"""

from __future__ import annotations

import datetime as _dt
import threading
from pathlib import Path

import pytest
import yaml
from django.db import connection
from rest_framework.test import APIClient

from adjudik_core import load_definition
from apps.accounts.models import Role
from apps.decisions.models import Decision, DecisionVersion, VersionStatus
from apps.evaluations.models import DecidedBy, Evaluation, EvaluationStatus
from apps.reviews import tasks as review_tasks
from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


def _demo_defn() -> dict:
    d = yaml.safe_load((EXAMPLES / "support_routing" / "decision.yaml").read_text())
    d["model_step"]["provider"] = "demo"
    d["model_step"]["model"] = "demo-1"
    return d


def _active_version(workspace, now):
    compiled = load_definition(_demo_defn())
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


def _pending_eval(
    workspace,
    decision,
    version,
    now,
    *,
    blind=False,
    suggested="billing",
    reason="low_confidence",
    kind=ReviewKind.GATE,
    status=EvaluationStatus.PENDING_REVIEW,
    external_id="ext-1",
):
    evaluation = Evaluation.objects.create(
        workspace=workspace,
        decision=decision,
        decision_version=version,
        external_id=external_id,
        salt=b"0" * 32,
        input={
            "ticket": {"id": "T-1", "subject": "s", "body": "b", "form": "contact", "plan": "free"}
        },
        status=status,
        state_version=1,
        handed_to_model=True,
        suggested_outcome=suggested,
        raw_confidence=0.9 if suggested else None,
        model_rationale="because reasons" if suggested else None,
        outcome="billing" if status == EvaluationStatus.DECIDED else None,
        decided_by=DecidedBy.MODEL if status == EvaluationStatus.DECIDED else None,
        created_at=now,
        updated_at=now,
        decided_at=now if status == EvaluationStatus.DECIDED else None,
    )
    task = ReviewTask.objects.create(
        workspace=workspace,
        decision=decision,
        evaluation=evaluation,
        kind=kind,
        priority=0 if kind == ReviewKind.GATE else 1,
        reason=reason if kind == ReviewKind.GATE else "audit_sample",
        suggested_outcome=suggested,
        suggested_confidence=None if blind else 0.9,
        blind=blind,
        label_propensity=0.25 if blind else None,
        due_at=now + _dt.timedelta(minutes=240),
        created_at=now,
    )
    return evaluation, task


def _client(workspace, make_member, role, username):
    user = make_member(workspace, role, username=username)
    client = APIClient()
    client.force_login(user)
    return client, user


# --- next / claim ---


@pytest.mark.django_db
def test_next_claims_and_returns_own_claim(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _pending_eval(workspace, decision, version, now)
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-1")

    r = client.post("/api/v1/reviews/next", {}, format="json")
    assert r.status_code == 200, r.content
    task_id = r.json()["id"]
    assert r.json()["status"] == "claimed"

    # A second next returns the same, still-held task.
    r2 = client.post("/api/v1/reviews/next", {}, format="json")
    assert r2.status_code == 200 and r2.json()["id"] == task_id


@pytest.mark.django_db
def test_next_204_when_empty(api_client, workspace, make_member, now):
    _active_version(workspace, now)
    client, _ = _client(workspace, make_member, Role.REVIEWER, "rev-e")
    r = client.post("/api/v1/reviews/next", {}, format="json")
    assert r.status_code == 204


@pytest.mark.django_db
def test_one_claim_per_reviewer(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _pending_eval(workspace, decision, version, now, external_id="a")
    _pending_eval(workspace, decision, version, now, external_id="b")
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-2")

    first = client.post("/api/v1/reviews/next", {}, format="json").json()["id"]
    # The reviewer already holds one; next returns that same one, never a second.
    second = client.post("/api/v1/reviews/next", {}, format="json").json()["id"]
    assert first == second
    assert ReviewTask.objects.filter(claimed_by=user, status=ReviewStatus.CLAIMED).count() == 1


@pytest.mark.django_db
def test_claim_by_id_forbidden_in_next_only(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now)
    workspace.settings.reviewer_claim_mode = "next_only"
    workspace.settings.save(update_fields=["reviewer_claim_mode"])
    client, _ = _client(workspace, make_member, Role.REVIEWER, "rev-no")
    r = client.post(f"/api/v1/reviews/{task.id}/claim", {}, format="json")
    assert r.status_code == 403


@pytest.mark.django_db
def test_next_only_hides_external_id_until_claimed(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now, external_id="secret-ext")
    workspace.settings.reviewer_claim_mode = "next_only"
    workspace.settings.save(update_fields=["reviewer_claim_mode"])
    client, _ = _client(workspace, make_member, Role.ADMIN, "adm-no")

    listed = client.get("/api/v1/reviews").json()["results"]
    assert listed[0]["external_id"] is None  # open task under next_only

    claimed = client.post("/api/v1/reviews/next", {}, format="json").json()
    assert claimed["case"]["external_id"] == "secret-ext"


# --- heartbeat / release / lease expiry ---


@pytest.mark.django_db
def test_heartbeat_extends_and_release_returns_to_open(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now)
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-hb")
    client.post("/api/v1/reviews/next", {}, format="json")

    hb = client.post(f"/api/v1/reviews/{task.id}/heartbeat", {}, format="json")
    assert hb.status_code == 200 and hb.json()["lease_expires_at"] is not None

    rel = client.post(f"/api/v1/reviews/{task.id}/release", {}, format="json")
    assert rel.status_code == 200 and rel.json()["status"] == "open"


@pytest.mark.django_db
def test_lease_expiry_job_releases(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now)
    user = make_member(workspace, Role.REVIEWER, username="rev-lease")
    task.status = ReviewStatus.CLAIMED
    task.claimed_by = user
    task.claimed_at = now
    task.claim_via = "next"
    task.lease_expires_at = now - _dt.timedelta(minutes=1)
    task.save()

    released = review_tasks.release_expired_leases(now=now)
    assert released == 1
    task.refresh_from_db()
    assert task.status == ReviewStatus.OPEN and task.claimed_by is None


@pytest.mark.django_db
def test_heartbeat_after_expiry_is_lease_lost(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now)
    user = make_member(workspace, Role.REVIEWER, username="rev-lost")
    task.status = ReviewStatus.CLAIMED
    task.claimed_by = user
    task.lease_expires_at = now - _dt.timedelta(minutes=1)
    task.save()
    client = APIClient()
    client.force_login(user)
    r = client.post(f"/api/v1/reviews/{task.id}/heartbeat", {}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "lease_lost"


# --- complete ---


@pytest.mark.django_db
def test_complete_accept_decides_evaluation(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    evaluation, task = _pending_eval(workspace, decision, version, now, suggested="billing")
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-acc")
    client.post("/api/v1/reviews/next", {}, format="json")

    r = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "accept", "outcome": "billing", "reason_codes": ["clear_match"]},
        format="json",
    )
    assert r.status_code == 200, r.content
    evaluation.refresh_from_db()
    assert evaluation.status == EvaluationStatus.DECIDED
    assert evaluation.decided_by == "human" and evaluation.outcome == "billing"
    task.refresh_from_db()
    assert task.agrees_with_model is True


@pytest.mark.django_db
def test_complete_override_requires_reason_text(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    evaluation, task = _pending_eval(workspace, decision, version, now, suggested="billing")
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-ovr")
    client.post("/api/v1/reviews/next", {}, format="json")

    missing = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "override", "outcome": "technical", "reason_codes": ["ambiguous"]},
        format="json",
    )
    assert missing.status_code == 422

    ok = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {
            "action": "override",
            "outcome": "technical",
            "reason_text": "actually a bug report",
            "reason_codes": ["ambiguous"],
        },
        format="json",
    )
    assert ok.status_code == 200
    evaluation.refresh_from_db()
    assert evaluation.outcome == "technical"
    task.refresh_from_db()
    assert task.agrees_with_model is False


@pytest.mark.django_db
def test_complete_requires_declared_reason_code(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    _, task = _pending_eval(workspace, decision, version, now, suggested="billing")
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-rc")
    client.post("/api/v1/reviews/next", {}, format="json")

    none_given = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "accept", "outcome": "billing"},
        format="json",
    )
    assert none_given.status_code == 422

    unknown = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "accept", "outcome": "billing", "reason_codes": ["nope"]},
        format="json",
    )
    assert unknown.status_code == 422


@pytest.mark.django_db
def test_blind_task_redaction_and_decide(api_client, workspace, make_member, now):
    decision, version = _active_version(workspace, now)
    evaluation, task = _pending_eval(
        workspace, decision, version, now, blind=True, suggested="billing"
    )
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-blind")
    detail = client.post("/api/v1/reviews/next", {}, format="json").json()
    # Blind: the suggestion is never shown.
    assert detail["blind"] is True
    assert detail["suggestion"] is None

    # accept/override are refused; the reviewer must decide.
    bad = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "accept", "outcome": "billing", "reason_codes": ["clear_match"]},
        format="json",
    )
    assert bad.status_code == 422

    ok = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "decide", "outcome": "billing", "reason_codes": ["clear_match"]},
        format="json",
    )
    assert ok.status_code == 200
    task.refresh_from_db()
    # agreement is still recorded on blind tasks.
    assert task.agrees_with_model is True


@pytest.mark.django_db
def test_audit_disagreement_writes_event(api_client, workspace, make_member, now):
    from apps.audit.models import AuditEvent

    decision, version = _active_version(workspace, now)
    evaluation, task = _pending_eval(
        workspace,
        decision,
        version,
        now,
        blind=True,
        suggested="billing",
        kind=ReviewKind.AUDIT,
        status=EvaluationStatus.DECIDED,
    )
    client, user = _client(workspace, make_member, Role.REVIEWER, "rev-audit")
    client.post("/api/v1/reviews/next", {"kind": "audit"}, format="json")

    r = client.post(
        f"/api/v1/reviews/{task.id}/complete",
        {"action": "decide", "outcome": "technical", "reason_codes": ["ambiguous"]},
        format="json",
    )
    assert r.status_code == 200
    # The evaluation is unchanged by an audit task.
    evaluation.refresh_from_db()
    assert evaluation.status == EvaluationStatus.DECIDED and evaluation.outcome == "billing"
    assert AuditEvent.objects.filter(
        workspace_id=workspace.id, event_type="audit.disagreement"
    ).exists()


@pytest.mark.django_db(transaction=True)
def test_parallel_next_gives_distinct_tasks(now):
    from apps.accounts import service as accounts_service

    workspace = accounts_service.create_workspace("parws", "Par", now=now)
    decision, version = _active_version(workspace, now)
    _pending_eval(workspace, decision, version, now, external_id="p1")
    _pending_eval(workspace, decision, version, now, external_id="p2")

    from django.contrib.auth import get_user_model

    from apps.accounts.models import Membership

    model = get_user_model()
    users = []
    for i in range(2):
        u = model.objects.create_user(username=f"par-{i}", password="x" * 12)
        Membership.objects.create(workspace=workspace, user=u, role=Role.REVIEWER, created_at=now)
        users.append(u)

    barrier = threading.Barrier(2)
    got: list[str] = []
    errors: list[Exception] = []

    def run(user) -> None:
        client = APIClient()
        client.force_login(user)
        try:
            barrier.wait()
            r = client.post("/api/v1/reviews/next", {}, format="json")
            if r.status_code == 200:
                got.append(r.json()["id"])
            else:
                errors.append(AssertionError(f"status {r.status_code}"))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(u,)) for u in users]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert len(got) == 2 and got[0] != got[1]  # no task claimed twice
