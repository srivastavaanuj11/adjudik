"""Decisions, versions and policies (SPEC §11.2, §11.8, §17 M3a).

Covers YAML and JSON upload, validate, multiple drafts, publish with rule 11 and
four-eyes, retire, carry-over, dry-run, render-prompt, acknowledge-model, and the
manual-policy lifecycle with floors and four-eyes.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from django.core.cache import cache

from apps.accounts.models import Role
from apps.decisions.models import (
    AutomationPolicy,
    Decision,
    DecisionVersion,
    PolicyStatus,
    VersionStatus,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def defn_yaml() -> str:
    return (EXAMPLES / "support_routing" / "decision.yaml").read_text()


@pytest.fixture
def defn_dict(defn_yaml) -> dict:
    return yaml.safe_load(defn_yaml)


@pytest.fixture
def demo_defn(defn_dict) -> dict:
    """The example wired to the in-process ``demo`` fake provider."""
    d = copy.deepcopy(defn_dict)
    d["model_step"]["provider"] = "demo"
    d["model_step"]["model"] = "demo-1"
    return d


def _make_decision(workspace, now, key="support_routing"):
    return Decision.objects.create(workspace=workspace, key=key, name="Support", created_at=now)


@pytest.mark.django_db
def test_yaml_and_json_upload_and_multiple_drafts(
    api_client, workspace, make_member, now, defn_yaml, defn_dict
):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)

    r = api_client.post(
        "/api/v1/decisions/support_routing/versions",
        data=defn_yaml,
        content_type="application/yaml",
    )
    assert r.status_code == 201, r.content
    assert r.json()["version"] == 1 and r.json()["status"] == "draft"

    r = api_client.post(
        "/api/v1/decisions/support_routing/versions",
        {"definition": defn_dict},
        format="json",
    )
    assert r.status_code == 201
    assert r.json()["version"] == 2  # a second draft coexists

    assert DecisionVersion.objects.filter(decision__key="support_routing").count() == 2


@pytest.mark.django_db
def test_validate_reports_errors(api_client, workspace, make_member, defn_dict):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)

    r = api_client.post("/api/v1/decisions/validate", {"definition": defn_dict}, format="json")
    assert r.status_code == 200 and r.json()["valid"] is True

    broken = copy.deepcopy(defn_dict)
    broken["outcomes"] = [broken["outcomes"][0]]  # fewer than 2 outcomes
    r = api_client.post("/api/v1/decisions/validate", {"definition": broken}, format="json")
    assert r.status_code == 200 and r.json()["valid"] is False
    assert r.json()["errors"]


@pytest.mark.django_db
def test_publish_sets_fingerprint_and_retires_previous(
    api_client, workspace, make_member, now, demo_defn
):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)

    for _ in range(2):
        api_client.post(
            "/api/v1/decisions/support_routing/versions",
            {"definition": demo_defn},
            format="json",
        )

    r = api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "active"
    assert r.json()["model_step_fingerprint"]  # computed against the configured provider
    assert Decision.objects.get(key="support_routing").name == demo_defn["name"]

    # Publishing v2 retires v1.
    r = api_client.post("/api/v1/decisions/support_routing/versions/2/publish", {}, format="json")
    assert r.status_code == 200
    assert DecisionVersion.objects.get(version=1).status == VersionStatus.RETIRED
    assert DecisionVersion.objects.get(version=2).status == VersionStatus.ACTIVE


@pytest.mark.django_db
def test_publish_rule_11_rejects_unknown_provider(
    api_client, workspace, make_member, now, defn_dict
):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    bad = copy.deepcopy(defn_dict)
    bad["model_step"]["provider"] = "nope"
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": bad}, format="json"
    )
    r = api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    assert r.status_code == 422
    assert r.json()["error"]["details"][0]["code"] == "provider_not_configured"


@pytest.mark.django_db
def test_four_eyes_publish(api_client, workspace, make_member, now, demo_defn):
    admin1 = make_member(workspace, Role.ADMIN, username="a1")
    admin2 = make_member(workspace, Role.ADMIN, username="a2")
    workspace.settings.require_four_eyes = True
    workspace.settings.save(update_fields=["require_four_eyes"])
    _make_decision(workspace, now)

    api_client.force_login(admin1)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    # The drafter cannot publish their own draft under four-eyes.
    r = api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "four_eyes_required"

    api_client.force_login(admin2)
    r = api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    assert r.status_code == 200


@pytest.mark.django_db
def test_retire_deactivates_active_policy(api_client, workspace, make_member, now, demo_defn):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    api_client.post(
        "/api/v1/decisions/support_routing/versions/1/policies",
        {"thresholds": {"billing": 0.95}, "audit_sample_rate": 0.1},
        format="json",
    )
    r = api_client.post("/api/v1/decisions/support_routing/versions/1/retire", {}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "retired"
    assert not AutomationPolicy.objects.filter(status=PolicyStatus.ACTIVE).exists()


@pytest.mark.django_db
def test_carry_over_policy_on_publish(api_client, workspace, make_member, now, demo_defn):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    for _ in range(2):
        api_client.post(
            "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
        )
    api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    api_client.post(
        "/api/v1/decisions/support_routing/versions/1/policies",
        {"thresholds": {"billing": 0.9}, "audit_sample_rate": 0.1},
        format="json",
    )
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/2/publish",
        {"carry_over_policy": True},
        format="json",
    )
    assert r.status_code == 200
    v2 = DecisionVersion.objects.get(version=2)
    carried = AutomationPolicy.objects.get(decision_version=v2, status=PolicyStatus.ACTIVE)
    assert carried.source == "carried_over"
    assert carried.thresholds == {"billing": 0.9}


@pytest.mark.django_db
def test_dry_run_rule_decided_and_render_prompt(api_client, workspace, make_member, now, demo_defn):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    # A refund_form ticket is decided by a rule (no model).
    rule_input = {
        "ticket": {
            "id": "T-1",
            "subject": "hi",
            "body": "please refund my invoice",
            "form": "refund_form",
            "plan": "free",
        }
    }
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/dry-run",
        {"input": rule_input},
        format="json",
    )
    assert r.status_code == 200, r.content
    assert r.json()["status"] == "decided" and r.json()["outcome"] == "billing"
    assert r.json()["model"] is None

    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/render-prompt",
        {"input": rule_input},
        format="json",
    )
    assert r.status_code == 200 and r.json()["system"] and r.json()["user"]


@pytest.mark.django_db
def test_dry_run_reaches_model_via_fake_provider(
    api_client, workspace, make_member, now, demo_defn
):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    model_input = {
        "ticket": {
            "id": "T-2",
            "subject": "charge",
            "body": "I was billed twice, need a refund of the invoice",
            "form": "contact",
            "plan": "free",
        }
    }
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/dry-run",
        {"input": model_input},
        format="json",
    )
    assert r.status_code == 200, r.content
    body = r.json()
    assert body["model"] is not None and body["model"]["ok"] is True
    # No active policy -> automation disabled, suggestion present.
    assert body["status"] == "needs_review" and body["route_reason"] == "automation_disabled"


@pytest.mark.django_db
def test_acknowledge_model_clears_observed(api_client, workspace, make_member, now, demo_defn):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    _make_decision(workspace, now)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")
    DecisionVersion.objects.filter(version=1).update(observed_model="hash-abc")
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/acknowledge-model", {}, format="json"
    )
    assert r.status_code == 200
    assert DecisionVersion.objects.get(version=1).observed_model is None


@pytest.mark.django_db
def test_policy_floor_and_four_eyes(api_client, workspace, make_member, now, demo_defn):
    admin1 = make_member(workspace, Role.ADMIN, username="p1")
    admin2 = make_member(workspace, Role.ADMIN, username="p2")
    _make_decision(workspace, now)
    api_client.force_login(admin1)
    api_client.post(
        "/api/v1/decisions/support_routing/versions", {"definition": demo_defn}, format="json"
    )
    api_client.force_login(admin2)
    api_client.post("/api/v1/decisions/support_routing/versions/1/publish", {}, format="json")

    # Below the min_threshold floor (0.80) -> policy_invalid.
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/policies",
        {"thresholds": {"billing": 0.5}, "audit_sample_rate": 0.1},
        format="json",
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "policy_invalid"

    workspace.settings.require_four_eyes = True
    workspace.settings.save(update_fields=["require_four_eyes"])

    api_client.force_login(admin1)
    r = api_client.post(
        "/api/v1/decisions/support_routing/versions/1/policies",
        {"thresholds": {"billing": 0.95}, "audit_sample_rate": 0.1},
        format="json",
    )
    assert r.status_code == 201 and r.json()["status"] == "proposed"
    policy_id = r.json()["id"]

    # The proposer cannot approve under four-eyes.
    r = api_client.post(f"/api/v1/policies/{policy_id}/approve", {}, format="json")
    assert r.status_code == 409 and r.json()["error"]["code"] == "four_eyes_required"

    api_client.force_login(admin2)
    r = api_client.post(f"/api/v1/policies/{policy_id}/approve", {}, format="json")
    assert r.status_code == 200 and r.json()["status"] == "active"


@pytest.mark.django_db
def test_decision_in_other_workspace_is_404(
    api_client, workspace, other_workspace, make_member, now, demo_defn
):
    _make_decision(other_workspace, now)
    admin = make_member(workspace, Role.ADMIN)  # member of acme only
    api_client.force_login(admin)
    r = api_client.get("/api/v1/decisions/support_routing")
    assert r.status_code == 404
