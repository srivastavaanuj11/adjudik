"""Webhooks: outbox, dispatcher, retry schedule, signatures, SSRF (SPEC §11.6, §17 M3b)."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

import pytest
import yaml

from adjudik_core import load_definition
from apps.accounts import service as accounts_service
from apps.common import crypto
from apps.decisions.models import Decision, DecisionVersion, VersionStatus
from apps.webhooks import sender, service
from apps.webhooks.models import DeliveryStatus, WebhookDelivery, WebhookEndpoint

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


def _endpoint(workspace, now, *, event_types, decision=None, secret="whsec_live"):
    return WebhookEndpoint.objects.create(
        workspace=workspace,
        decision=decision,
        url="https://hook.example.com/in",
        secret_encrypted=crypto.encrypt(secret),
        event_types=event_types,
        active=True,
        created_at=now,
    )


def _delivery(workspace, endpoint, now, *, status=DeliveryStatus.PENDING, attempt=0):
    return WebhookDelivery.objects.create(
        workspace=workspace,
        endpoint=endpoint,
        event_id="11111111-1111-1111-1111-111111111111",
        event_type="evaluation.decided",
        payload={"id": "e", "type": "evaluation.decided", "data": {}},
        status=status,
        attempt=attempt,
        next_attempt_at=now,
        created_at=now,
    )


# --- outbox in the same transaction (SPEC §11.6) ---


@pytest.mark.django_db
def test_rule_decided_inserts_outbox_row(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    _endpoint(workspace, now, event_types=["evaluation.decided"])
    issued = accounts_service.issue_api_key(
        workspace, name="k", scopes=["evaluate"], decision_ids=None, now=now, created_by=None
    )
    r = api_client.post(
        "/api/v1/decisions/support_routing/evaluate",
        {
            "input": {
                "ticket": {
                    "id": "T",
                    "subject": "s",
                    "body": "b",
                    "form": "refund_form",
                    "plan": "free",
                }
            }
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
    )
    assert r.status_code == 200
    rows = WebhookDelivery.objects.filter(workspace=workspace, event_type="evaluation.decided")
    assert rows.count() == 1
    payload = rows.first().payload
    # Thin payload: no inputs, metadata or rationale.
    assert set(payload["data"]) == {
        "evaluation_id",
        "decision",
        "version",
        "external_id",
        "status",
        "outcome",
        "decided_by",
        "route_reason",
        "state_version",
    }
    assert "input" not in payload and "metadata" not in payload


@pytest.mark.django_db
def test_endpoint_decision_scope_filters(api_client, workspace, now):
    decision, version = _active_version(workspace, now)
    other = Decision.objects.create(workspace=workspace, key="other", name="Other", created_at=now)
    _endpoint(workspace, now, event_types=["evaluation.decided"], decision=other)
    issued = accounts_service.issue_api_key(
        workspace, name="k", scopes=["evaluate"], decision_ids=None, now=now, created_by=None
    )
    api_client.post(
        "/api/v1/decisions/support_routing/evaluate",
        {
            "input": {
                "ticket": {
                    "id": "T",
                    "subject": "s",
                    "body": "b",
                    "form": "refund_form",
                    "plan": "free",
                }
            }
        },
        format="json",
        HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
    )
    # The endpoint is bound to a different decision, so no row.
    assert not WebhookDelivery.objects.filter(workspace=workspace).exists()


# --- dispatcher and retry schedule (SPEC §11.6) ---


@pytest.mark.django_db
def test_dispatch_success(workspace, now):
    endpoint = _endpoint(workspace, now, event_types=["evaluation.decided"])
    delivery = _delivery(workspace, endpoint, now)

    def ok(d, *, now):
        return sender.Outcome(status_code=200, error_class=None)

    result = service.dispatch_due(now=now, deliver=ok)
    assert result["sent"] == 1
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SUCCEEDED
    assert delivery.delivered_at is not None and delivery.attempt == 1


@pytest.mark.django_db
def test_retry_schedule_then_dead(workspace, now):
    endpoint = _endpoint(workspace, now, event_types=["evaluation.decided"])
    delivery = _delivery(workspace, endpoint, now)

    def fail(d, *, now):
        return sender.Outcome(status_code=500, error_class="http_error")

    expected = [10, 60, 300, 1800, 7200, 21600]
    at = now
    for i, delay in enumerate(expected, start=1):
        service.dispatch_due(now=at, deliver=fail)
        delivery.refresh_from_db()
        assert delivery.attempt == i
        assert delivery.status == DeliveryStatus.FAILED
        assert delivery.next_attempt_at == at + _dt.timedelta(seconds=delay)
        at = delivery.next_attempt_at  # advance to the next due time

    # The 7th failure marks it dead.
    service.dispatch_due(now=at, deliver=fail)
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DEAD and delivery.next_attempt_at is None


@pytest.mark.django_db
def test_dispatch_skips_not_yet_due(workspace, now):
    endpoint = _endpoint(workspace, now, event_types=["evaluation.decided"])
    future = now + _dt.timedelta(minutes=10)
    WebhookDelivery.objects.create(
        workspace=workspace,
        endpoint=endpoint,
        event_id="22222222-2222-2222-2222-222222222222",
        event_type="evaluation.decided",
        payload={"data": {}},
        status=DeliveryStatus.FAILED,
        attempt=1,
        next_attempt_at=future,
        created_at=now,
    )
    assert service.dispatch_due(now=now, deliver=lambda d, now: None) == {
        "sent": 0,
        "failed": 0,
        "dead": 0,
    }


# --- signatures and rotation (SPEC §11.6) ---


@pytest.mark.django_db
def test_signature_single_then_two_during_rotation(workspace, now):
    endpoint = _endpoint(workspace, now, event_types=["evaluation.decided"], secret="whsec_new")
    body = b'{"a":1}'
    ts = int(now.timestamp())
    header = sender.signature_header(endpoint, ts, body, now=now)
    assert header.count("v1=") == 1

    endpoint.previous_secret_encrypted = crypto.encrypt("whsec_old")
    endpoint.previous_secret_expires_at = now + _dt.timedelta(hours=1)
    endpoint.save()
    header2 = sender.signature_header(endpoint, ts, body, now=now)
    assert header2.count("v1=") == 2

    # Expired grace -> back to one signature.
    endpoint.previous_secret_expires_at = now - _dt.timedelta(minutes=1)
    endpoint.save()
    assert sender.signature_header(endpoint, ts, body, now=now).count("v1=") == 1


# --- SSRF: DNS that changes between attempts (SPEC §11.6) ---


@pytest.mark.django_db
def test_blocked_when_dns_flips_to_private(workspace, now, monkeypatch):
    endpoint = _endpoint(workspace, now, event_types=["evaluation.decided"])
    delivery = _delivery(workspace, endpoint, now)

    calls = {"n": 0}

    def flipping_resolve(host):
        calls["n"] += 1
        return ["93.184.216.34"] if calls["n"] == 1 else ["10.0.0.5"]

    sent = {"n": 0}

    def fake_send(host, port, ip, path, body, headers):
        sent["n"] += 1
        return 200

    monkeypatch.setattr(sender, "_resolve", flipping_resolve)
    monkeypatch.setattr(sender, "_send", fake_send)

    # Attempt 1: public address -> delivered.
    out1 = sender.deliver(delivery, now=now)
    assert out1.ok and sent["n"] == 1

    # Attempt 2: the record now points at a private address -> blocked, no send.
    out2 = sender.deliver(delivery, now=now)
    assert out2.error_class == "blocked_address" and sent["n"] == 1


@pytest.mark.django_db
def test_rejects_non_https_and_bad_port(workspace, now):
    with pytest.raises(sender.WebhookError) as e1:
        sender.check_url("http://hook.example.com/in")
    assert e1.value.error_class == "blocked_address"
    with pytest.raises(sender.WebhookError) as e2:
        sender.check_url("https://hook.example.com:9999/in")
    assert e2.value.error_class == "blocked_address"


# --- management endpoints (SPEC §11.2) ---


@pytest.mark.django_db
def test_create_rotate_and_retry(api_client, workspace, make_member, now):
    from rest_framework.test import APIClient

    admin = make_member(workspace, "admin", username="wh-admin")
    client = APIClient()
    client.force_login(admin)

    created = client.post(
        "/api/v1/webhooks",
        {"url": "https://hook.example.com/in", "event_types": ["evaluation.decided"]},
        format="json",
    )
    assert created.status_code == 201
    assert created.json()["secret"].startswith("whsec_")  # returned once
    endpoint_id = created.json()["id"]

    rotated = client.post(f"/api/v1/webhooks/{endpoint_id}/rotate-secret", {}, format="json")
    assert rotated.status_code == 200 and rotated.json()["secret"].startswith("whsec_")
    endpoint = WebhookEndpoint.objects.get(id=endpoint_id)
    assert endpoint.previous_secret_encrypted and endpoint.previous_secret_expires_at
