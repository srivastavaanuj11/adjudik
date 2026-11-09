"""M0: health endpoints (SPEC §11.2, §17)."""

from __future__ import annotations

import pytest
from django.test import Client


def test_healthz_is_200_without_dependencies() -> None:
    resp = Client().get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.django_db
def test_readyz_200_when_dependencies_up() -> None:
    resp = Client().get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["checks"]["database"] == "ok"
    assert body["checks"]["redis"] == "ok"


@pytest.mark.django_db
def test_readyz_503_when_redis_down(settings) -> None:
    # Point Redis at a closed port so the ping fails fast.
    settings.REDIS_URL = "redis://127.0.0.1:1/0"
    resp = Client().get("/readyz")
    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "unavailable"
    assert body["checks"]["redis"].startswith("error")
