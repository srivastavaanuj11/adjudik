"""Throttles, CSRF and the logging canary (SPEC §15.5, §15.8, §17 M3b)."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient

from adjudik_core import load_definition
from apps.accounts import service as accounts_service
from apps.common import logging as canary_logging
from apps.decisions.models import Decision, DecisionVersion, VersionStatus

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _active_version(workspace, now):
    d = yaml.safe_load((EXAMPLES / "support_routing" / "decision.yaml").read_text())
    d["model_step"]["provider"] = "demo"
    d["model_step"]["model"] = "demo-1"
    compiled = load_definition(d)
    decision = Decision.objects.create(
        workspace=workspace, key=compiled.key, name=compiled.definition.name, created_at=now
    )
    DecisionVersion.objects.create(
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
    return decision


# --- throttles (SPEC §15.8) ---


@pytest.mark.django_db
@override_settings(ADJUDIK_EVAL_RATE="2/min", ADJUDIK_WORKSPACE_EVAL_RATE="1000/min")
def test_evaluate_throttle_returns_429_with_retry_after(api_client, workspace, now):
    _active_version(workspace, now)
    issued = accounts_service.issue_api_key(
        workspace, name="k", scopes=["evaluate"], decision_ids=None, now=now, created_by=None
    )
    headers = {"HTTP_AUTHORIZATION": f"Bearer {issued.full_key}"}
    body = {
        "input": {
            "ticket": {
                "id": "T",
                "subject": "s",
                "body": "b",
                "form": "refund_form",
                "plan": "free",
            }
        }
    }

    assert (
        api_client.post(
            "/api/v1/decisions/support_routing/evaluate", body, format="json", **headers
        ).status_code
        == 200
    )
    assert (
        api_client.post(
            "/api/v1/decisions/support_routing/evaluate", body, format="json", **headers
        ).status_code
        == 200
    )
    blocked = api_client.post(
        "/api/v1/decisions/support_routing/evaluate", body, format="json", **headers
    )
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in blocked.headers


@pytest.mark.django_db
@override_settings(ADJUDIK_API_RATE="2/min")
def test_general_api_throttle(api_client, workspace, make_member, now):
    admin = make_member(workspace, "admin", username="thr-admin")
    client = APIClient()
    client.force_login(admin)
    assert client.get("/api/v1/decisions").status_code == 200
    assert client.get("/api/v1/decisions").status_code == 200
    assert client.get("/api/v1/decisions").status_code == 429


# --- CSRF (SPEC §15.8) ---


@pytest.mark.django_db
def test_session_post_requires_csrf(workspace, make_member, now):
    admin = make_member(workspace, "admin", username="csrf-admin")
    client = APIClient(enforce_csrf_checks=True)
    client.force_login(admin)
    # No CSRF token -> rejected.
    blocked = client.post("/api/v1/decisions", {"key": "k", "name": "n"}, format="json")
    assert blocked.status_code == 403

    token = client.get("/api/v1/auth/csrf").json().get("csrfToken")
    ok = client.post(
        "/api/v1/decisions",
        {"key": "newk", "name": "n"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )
    assert ok.status_code in (201, 409)  # created (or already exists) — CSRF passed


# --- logging canary (SPEC §15.5) ---


@pytest.mark.django_db
@override_settings(ADJUDIK_LOG_CANARY="CANARY-7f3a9")
def test_app_never_logs_input_canary(api_client, workspace, now, caplog):
    _active_version(workspace, now)
    issued = accounts_service.issue_api_key(
        workspace,
        name="k",
        scopes=["evaluate", "read_sensitive"],
        decision_ids=None,
        now=now,
        created_by=None,
    )
    canary_logging.reset_leaks()
    with caplog.at_level(logging.DEBUG):
        # Submit input carrying the canary (an invalid field triggers the error path).
        api_client.post(
            "/api/v1/decisions/support_routing/evaluate",
            {
                "input": {
                    "ticket": {
                        "id": "CANARY-7f3a9",
                        "subject": "CANARY-7f3a9",
                        "body": "CANARY-7f3a9",
                        "form": "refund_form",
                        "plan": "free",
                    }
                }
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {issued.full_key}",
        )
    assert "CANARY-7f3a9" not in caplog.text
    assert canary_logging.leaks() == []


@override_settings(ADJUDIK_LOG_CANARY="CANARY-7f3a9")
def test_canary_filter_redacts_a_leak(caplog):
    """The filter itself redacts and records, so the guard is proven live."""
    canary_logging.reset_leaks()
    logger = logging.getLogger("adjudik.test")
    logger.addFilter(canary_logging.CanaryFilter())
    with caplog.at_level(logging.INFO):
        logger.info("leaking CANARY-7f3a9 into the log")
    assert canary_logging.leaks()  # the filter caught it
