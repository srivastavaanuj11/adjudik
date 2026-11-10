"""Permission matrix and two-workspace isolation (SPEC §15.1, §17 M3a).

Every capability is exercised against a representative endpoint for each role and
key scope, and a second workspace's member is denied (404) every one of the
first workspace's ids. ``is_staff``/``is_superuser`` are never consulted.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from django.core.cache import cache
from rest_framework.test import APIClient

from adjudik_core import load_definition
from apps.accounts import service as accounts_service
from apps.accounts.models import Role
from apps.decisions.models import (
    AutomationPolicy,
    Decision,
    DecisionVersion,
    PolicySource,
    PolicyStatus,
    VersionStatus,
)

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


def _demo_defn() -> dict:
    d = yaml.safe_load((EXAMPLES / "support_routing" / "decision.yaml").read_text())
    d["model_step"]["provider"] = "demo"
    return d


def _active_version(workspace, now):
    compiled = load_definition(_demo_defn())
    decision = Decision.objects.create(
        workspace=workspace, key="support_routing", name="S", created_at=now
    )
    version = DecisionVersion.objects.create(
        workspace=workspace,
        decision=decision,
        version=1,
        status=VersionStatus.ACTIVE,
        definition=compiled.definition.model_dump(mode="json"),
        source_format="json",
        definition_hash=compiled.definition_hash,
        rules_hash=compiled.rules_hash or "",
        created_at=now,
        updated_at=now,
        published_at=now,
    )
    return decision, version


def _session(workspace, make_member, role):
    user = make_member(workspace, role, username=f"{role}-perm")
    client = APIClient()
    client.force_login(user)
    return client


def _key_client(full_key):
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {full_key}")
    return client


# capability -> (method, path, body) representative endpoint
CASES = {
    "read": ("get", "/api/v1/decisions", None),
    "manage": ("post", "/api/v1/decisions/validate", {"definition": _demo_defn()}),
    "publish": ("post", "/api/v1/decisions/support_routing/versions/1/publish", {}),
    "policy": (
        "post",
        "/api/v1/decisions/support_routing/versions/1/policies",
        {"thresholds": {"billing": 0.95}, "audit_sample_rate": 0.1},
    ),
    "audit": ("get", "/api/v1/audit/events", None),
    "admin": ("get", "/api/v1/members", None),
    "evaluate": ("post", "/api/v1/decisions/support_routing/evaluate", {"input": {}}),
}

# Which capabilities each session role is allowed (SPEC §15.1).
ROLE_ALLOWED = {
    Role.ADMIN: {"read", "manage", "publish", "policy", "audit", "admin"},
    Role.AUTHOR: {"read", "manage"},
    Role.REVIEWER: set(),
    Role.AUDITOR: {"read", "audit"},
}

# Which capabilities each API-key scope is allowed.
SCOPE_ALLOWED = {
    "evaluate": {"evaluate"},
    "read": {"read"},
    "read_sensitive": {"read"},
    "manage": {"manage"},
}


def _call(client, method, path, body):
    fn = getattr(client, method)
    if body is None:
        return fn(path)
    return fn(path, body, format="json")


@pytest.mark.django_db
@pytest.mark.parametrize("role", list(ROLE_ALLOWED))
def test_role_permission_matrix(api_client, workspace, make_member, now, role):
    _active_version(workspace, now)
    client = _session(workspace, make_member, role)
    for capability, (method, path, body) in CASES.items():
        r = _call(client, method, path, body)
        if capability in ROLE_ALLOWED[role]:
            assert r.status_code != 403, f"{role} should access {capability}: {r.status_code}"
        else:
            assert r.status_code == 403, f"{role} must be denied {capability}: {r.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize("scope", list(SCOPE_ALLOWED))
def test_key_scope_permission_matrix(workspace, now, scope):
    _active_version(workspace, now)
    issued = accounts_service.issue_api_key(
        workspace, name=scope, scopes=[scope], decision_ids=None, now=now, created_by=None
    )
    client = _key_client(issued.full_key)
    for capability, (method, path, body) in CASES.items():
        r = _call(client, method, path, body)
        if capability in SCOPE_ALLOWED[scope]:
            assert r.status_code != 403, f"{scope} should access {capability}: {r.status_code}"
        else:
            assert r.status_code == 403, f"{scope} must be denied {capability}: {r.status_code}"


@pytest.mark.django_db
def test_two_workspace_isolation(api_client, workspace, other_workspace, make_member, now):
    # Workspace A (other_workspace) owns everything; a member of B (workspace) probes A's ids.
    decision_a, version_a = _active_version(other_workspace, now)
    key_a = accounts_service.issue_api_key(
        other_workspace, name="ka", scopes=["read"], decision_ids=None, now=now, created_by=None
    )
    admin_a = make_member(other_workspace, Role.ADMIN, username="admin-a")
    from apps.accounts.models import Membership

    member_a = Membership.objects.get(workspace=other_workspace, user=admin_a)
    policy_a = AutomationPolicy.objects.create(
        workspace=other_workspace,
        decision_version=version_a,
        status=PolicyStatus.PROPOSED,
        thresholds={"billing": 0.95},
        targets={},
        audit_sample_rate=0.1,
        source=PolicySource.MANUAL,
        proposed_at=now,
    )

    admin_b = make_member(workspace, Role.ADMIN, username="admin-b")
    client = APIClient()
    client.force_login(admin_b)

    probes = [
        ("get", "/api/v1/decisions/support_routing"),
        ("get", "/api/v1/decisions/support_routing/versions/1"),
        ("patch", f"/api/v1/members/{member_a.id}"),
        ("post", f"/api/v1/api-keys/{key_a.api_key.id}/revoke"),
        ("post", f"/api/v1/policies/{policy_a.id}/approve"),
    ]
    for method, path in probes:
        fn = getattr(client, method)
        r = fn(path, {}, format="json") if method in ("post", "patch") else fn(path)
        assert r.status_code == 404, f"{method} {path} leaked across workspaces: {r.status_code}"


def test_no_staff_or_superuser_access_in_app_code():
    """`is_staff`/`is_superuser` are never *consulted* for authorization (SPEC §15.1).

    Scans for attribute access (``.is_staff`` / ``.is_superuser``); prose in
    comments that merely names the rule is allowed.
    """
    apps_dir = Path(__file__).resolve().parents[1] / "apps"
    offenders = []
    for py in apps_dir.rglob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), start=1):
            code = line.split("#", 1)[0]
            if ".is_staff" in code or ".is_superuser" in code:
                offenders.append(f"{py}:{lineno}")
    assert not offenders, offenders
