"""Evaluation cancel and the cancel-versus-complete race (SPEC §11.2, §17 M3b)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
import yaml
from django.db import connection
from rest_framework.test import APIClient

from adjudik_core import load_definition
from apps.accounts import service as accounts_service
from apps.accounts.models import Membership, Role
from apps.decisions.models import Decision, DecisionVersion, VersionStatus
from apps.evaluations.models import DecidedBy, Evaluation, EvaluationStatus
from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask
from apps.webhooks.models import WebhookDelivery, WebhookEndpoint

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


def _pending(workspace, decision, version, now, *, api_key_id=None, suggested="billing"):
    evaluation = Evaluation.objects.create(
        workspace=workspace,
        decision=decision,
        decision_version=version,
        external_id="c-1",
        salt=b"0" * 32,
        input={
            "ticket": {"id": "T", "subject": "s", "body": "b", "form": "contact", "plan": "free"}
        },
        status=EvaluationStatus.PENDING_REVIEW,
        state_version=1,
        handed_to_model=True,
        suggested_outcome=suggested,
        raw_confidence=0.9,
        created_by_api_key_id=api_key_id,
        created_at=now,
        updated_at=now,
    )
    task = ReviewTask.objects.create(
        workspace=workspace,
        decision=decision,
        evaluation=evaluation,
        kind=ReviewKind.GATE,
        priority=0,
        reason="low_confidence",
        suggested_outcome=suggested,
        suggested_confidence=0.9,
        due_at=now,
        created_at=now,
    )
    return evaluation, task


def _key(workspace, now):
    issued = accounts_service.issue_api_key(
        workspace,
        name="k",
        scopes=["evaluate", "read"],
        decision_ids=None,
        now=now,
        created_by=None,
    )
    return issued


@pytest.mark.django_db
def test_cancel_by_creating_key(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    issued = _key(workspace, now)
    evaluation, task = _pending(workspace, decision, version, now, api_key_id=issued.api_key.id)
    # An active endpoint subscribed to cancellations.
    WebhookEndpoint.objects.create(
        workspace=workspace,
        url="https://hook.example.com/x",
        secret_encrypted="x",
        event_types=["evaluation.cancelled"],
        active=True,
        created_at=now,
    )

    r = api_client.post(
        f"/api/v1/evaluations/{evaluation.id}/cancel",
        {"reason": "withdrawn_by_customer"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
    )
    assert r.status_code == 200, r.content
    evaluation.refresh_from_db()
    task.refresh_from_db()
    assert evaluation.status == EvaluationStatus.CANCELLED
    assert task.status == ReviewStatus.CANCELLED
    assert (
        WebhookDelivery.objects.filter(
            workspace=workspace, event_type="evaluation.cancelled"
        ).count()
        == 1
    )


@pytest.mark.django_db
def test_cancel_by_other_key_forbidden(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    creator = _key(workspace, now)
    other = _key(workspace, now)
    evaluation, _ = _pending(workspace, decision, version, now, api_key_id=creator.api_key.id)
    r = api_client.post(
        f"/api/v1/evaluations/{evaluation.id}/cancel",
        {"reason": "duplicate"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {other.full_key}",
    )
    assert r.status_code == 403


@pytest.mark.django_db
def test_cancel_from_decided_conflicts(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    issued = _key(workspace, now)
    evaluation, _ = _pending(workspace, decision, version, now, api_key_id=issued.api_key.id)
    evaluation.status = EvaluationStatus.DECIDED
    evaluation.decided_by = DecidedBy.MODEL
    evaluation.outcome = "billing"
    evaluation.decided_at = now
    evaluation.save()
    r = api_client.post(
        f"/api/v1/evaluations/{evaluation.id}/cancel",
        {"reason": "other"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "invalid_state"


@pytest.mark.django_db
def test_cancel_rejects_unknown_reason(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    issued = _key(workspace, now)
    evaluation, _ = _pending(workspace, decision, version, now, api_key_id=issued.api_key.id)
    r = api_client.post(
        f"/api/v1/evaluations/{evaluation.id}/cancel",
        {"reason": "because"},
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
    )
    assert r.status_code == 422


@pytest.mark.django_db(transaction=True)
def test_cancel_versus_complete_race(now):
    """Exactly one of cancel / complete wins; the loser gets a clean error (SPEC §8.5)."""
    workspace = accounts_service.create_workspace("raze", "Raze", now=now)
    decision, version = _active_version(workspace, now)
    issued = _key(workspace, now)
    evaluation, task = _pending(workspace, decision, version, now, api_key_id=issued.api_key.id)

    from django.contrib.auth import get_user_model

    reviewer = get_user_model().objects.create_user(username="race-rev", password="x" * 12)
    Membership.objects.create(
        workspace=workspace, user=reviewer, role=Role.REVIEWER, created_at=now
    )
    # The reviewer already holds the task with a live lease.
    task.status = ReviewStatus.CLAIMED
    task.claimed_by = reviewer
    task.claimed_at = now
    task.claim_via = "next"
    from django.utils import timezone

    task.lease_expires_at = timezone.now() + __import__("datetime").timedelta(minutes=30)
    task.save()

    barrier = threading.Barrier(2)
    results: dict[str, int] = {}
    errors: list[Exception] = []

    def do_cancel() -> None:
        client = APIClient()
        try:
            barrier.wait()
            r = client.post(
                f"/api/v1/evaluations/{evaluation.id}/cancel",
                {"reason": "duplicate"},
                format="json",
                HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
            )
            results["cancel"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    def do_complete() -> None:
        client = APIClient()
        client.force_login(reviewer)
        try:
            barrier.wait()
            r = client.post(
                f"/api/v1/reviews/{task.id}/complete",
                {"action": "accept", "outcome": "billing", "reason_codes": ["clear_match"]},
                format="json",
            )
            results["complete"] = r.status_code
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=do_cancel), threading.Thread(target=do_complete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    evaluation.refresh_from_db()
    # One request succeeded (200); the other hit a 409 (invalid_state or lease_lost).
    assert 200 in results.values()
    assert any(s == 409 for s in results.values())
    assert evaluation.status in (EvaluationStatus.DECIDED, EvaluationStatus.CANCELLED)
    if evaluation.status == EvaluationStatus.DECIDED:
        assert results["complete"] == 200 and results["cancel"] == 409
    else:
        assert results["cancel"] == 200 and results["complete"] == 409
