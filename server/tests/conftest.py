"""Shared server test fixtures (SPEC §16.4).

Tests run against real Postgres and Redis as the owner role. Immutability tests
open a *second* connection as ``adjudik_app`` (least privilege) to prove the
grant matrix and triggers. Services take an injected clock; timestamps are never
``auto_now_add``.
"""

from __future__ import annotations

import datetime as _dt

import psycopg
import pytest
from django.db import connection

from apps.accounts import service as accounts_service
from apps.common.clock import FixedClock

FIXED_INSTANT = _dt.datetime(2026, 11, 9, 10, 0, 0, tzinfo=_dt.UTC)


@pytest.fixture
def clock() -> FixedClock:
    return FixedClock(FIXED_INSTANT)


@pytest.fixture
def now() -> _dt.datetime:
    return FIXED_INSTANT


@pytest.fixture
def app_dsn() -> str:
    """psycopg DSN for the runtime (least-privilege) role on the test database."""
    d = connection.settings_dict
    return (
        f"postgresql://adjudik_app:adjudik_app_pw@{d['HOST'] or 'localhost'}:"
        f"{d['PORT'] or 5432}/{d['NAME']}"
    )


@pytest.fixture
def app_conn(app_dsn: str):
    """A connection as ``adjudik_app`` for privilege/immutability checks."""
    conn = psycopg.connect(app_dsn)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def workspace(db, now):
    return accounts_service.create_workspace("acme", "Acme", now=now)


@pytest.fixture
def other_workspace(db, now):
    return accounts_service.create_workspace("globex", "Globex", now=now)


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def make_member(db, now):
    """Create a global user with a membership of ``role`` in a workspace."""
    from django.contrib.auth import get_user_model

    from apps.accounts.models import Membership

    created: list = []

    def _make(
        workspace, role: str, username: str | None = None, password: str = "correct horse staple 12"
    ):
        model = get_user_model()
        username = username or f"{role}-{len(created)}"
        user = model.objects.create_user(username=username, password=password)
        Membership.objects.create(workspace=workspace, user=user, role=role, created_at=now)
        created.append(user)
        return user

    return _make
