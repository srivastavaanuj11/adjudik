"""Accounts: bootstrap, auth, members, API keys, settings (SPEC §15.2, §16.6, §17)."""

from __future__ import annotations

import datetime as _dt

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command

from apps.accounts.models import ApiKey, Membership, Role, UserPasswordState, Workspace


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_bootstrap_is_idempotent(monkeypatch):
    monkeypatch.setenv("ADJUDIK_BOOTSTRAP_PASSWORD", "a-very-long-temp-pass-123")
    call_command("adjudik_bootstrap", "--workspace", "acme", "--admin", "root")
    call_command("adjudik_bootstrap", "--workspace", "acme", "--admin", "root")  # again

    ws = Workspace.objects.get(slug="acme")
    assert ws.settings.min_threshold == 0.80
    user = get_user_model().objects.get(username="root")
    assert Membership.objects.filter(workspace=ws, user=user, role=Role.ADMIN).count() == 1
    assert UserPasswordState.objects.get(user=user).must_change is True


@pytest.mark.django_db
def test_bootstrap_requires_password(monkeypatch):
    monkeypatch.delenv("ADJUDIK_BOOTSTRAP_PASSWORD", raising=False)
    from django.core.management.base import CommandError

    with pytest.raises(CommandError):
        call_command("adjudik_bootstrap", "--workspace", "x", "--admin", "y")


@pytest.mark.django_db
def test_login_requires_password_change_then_succeeds(api_client, workspace, now):
    from apps.accounts import service

    user, temp = service.create_user_with_temp_password("alice", now=now)
    Membership.objects.create(workspace=workspace, user=user, role=Role.ADMIN, created_at=now)

    r = api_client.post(
        "/api/v1/auth/login", {"username": "alice", "password": temp}, format="json"
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "password_change_required"

    r = api_client.post(
        "/api/v1/auth/password", {"new_password": "brand-new-pass-4567"}, format="json"
    )
    assert r.status_code == 200

    api_client.logout()
    r = api_client.post(
        "/api/v1/auth/login",
        {"username": "alice", "password": "brand-new-pass-4567"},
        format="json",
    )
    assert r.status_code == 200
    assert r.json()["role"] == "admin"


@pytest.mark.django_db
def test_login_throttle_locks_out_after_five_failures(api_client, workspace, make_member):
    make_member(workspace, Role.ADMIN, username="bob", password="correct-horse-staple-12")
    for _ in range(5):
        r = api_client.post(
            "/api/v1/auth/login", {"username": "bob", "password": "wrong"}, format="json"
        )
        assert r.status_code == 401
    # 6th attempt (even with the right password) is locked out.
    r = api_client.post(
        "/api/v1/auth/login",
        {"username": "bob", "password": "correct-horse-staple-12"},
        format="json",
    )
    assert r.status_code == 429


@pytest.mark.django_db
def test_admin_adds_member_with_temp_password(api_client, workspace, make_member):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)

    r = api_client.post("/api/v1/members", {"username": "carol", "role": "reviewer"}, format="json")
    assert r.status_code == 201
    body = r.json()
    assert body["role"] == "reviewer"
    assert "temporary_password" in body  # returned once

    # Adding the same user again (as a member) conflicts.
    r = api_client.post("/api/v1/members", {"username": "carol", "role": "author"}, format="json")
    assert r.status_code == 409


@pytest.mark.django_db
def test_author_cannot_manage_members(api_client, workspace, make_member):
    author = make_member(workspace, Role.AUTHOR)
    api_client.force_login(author)
    r = api_client.get("/api/v1/members")
    assert r.status_code == 403


@pytest.mark.django_db
def test_api_key_create_authenticate_expire_rotate(api_client, workspace, make_member, now):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)

    r = api_client.post("/api/v1/api-keys", {"name": "ci", "scopes": ["read"]}, format="json")
    assert r.status_code == 201
    full = r.json()["key"]
    assert full.startswith("adk_")
    key_id = r.json()["id"]

    # The key authenticates a read request.
    anon = type(api_client)()
    r = anon.get("/api/v1/members", HTTP_AUTHORIZATION=f"Bearer {full}")
    # read scope lacks admin capability for members -> 403 (but authenticated, not 401).
    assert r.status_code == 403

    # Expire it in the past -> 401.
    ApiKey.objects.filter(id=key_id).update(expires_at=now - _dt.timedelta(days=1))
    r = anon.get("/api/v1/members", HTTP_AUTHORIZATION=f"Bearer {full}")
    assert r.status_code == 401

    # Rotate: old secret valid during the 24 h grace.
    ApiKey.objects.filter(id=key_id).update(expires_at=now + _dt.timedelta(days=30))
    r = api_client.post(f"/api/v1/api-keys/{key_id}/rotate", format="json")
    assert r.status_code == 200
    new_full = r.json()["key"]
    assert new_full != full
    # Both authenticate right after rotation.
    for token in (full, new_full):
        r = type(api_client)().get("/api/v1/members", HTTP_AUTHORIZATION=f"Bearer {token}")
        assert r.status_code in (403,)  # authenticated (not 401)

    # Revoke -> 401.
    r = api_client.post(f"/api/v1/api-keys/{key_id}/revoke", format="json")
    assert r.status_code == 200
    r = type(api_client)().get("/api/v1/members", HTTP_AUTHORIZATION=f"Bearer {new_full}")
    assert r.status_code == 401


@pytest.mark.django_db
def test_workspace_settings_get_patch_and_floor(api_client, workspace, make_member):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)

    r = api_client.get("/api/v1/workspace/settings")
    assert r.status_code == 200 and r.json()["min_threshold"] == 0.80

    r = api_client.patch("/api/v1/workspace/settings", {"min_threshold": 0.9}, format="json")
    assert r.status_code == 200 and r.json()["min_threshold"] == 0.9

    # Out of range.
    r = api_client.patch("/api/v1/workspace/settings", {"min_threshold": 0.3}, format="json")
    assert r.status_code == 422
