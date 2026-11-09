"""Liveness and readiness endpoints (SPEC §11.2).

Routed at the root, outside ``/api/v1``, no auth. ``/healthz`` is a pure liveness
probe. ``/readyz`` checks that the database and Redis are reachable and returns
503 if either is down.
"""

from __future__ import annotations

from django.conf import settings
from django.db import connection
from django.http import HttpRequest, JsonResponse


def healthz(request: HttpRequest) -> JsonResponse:
    """Liveness: the process is up. No dependency checks."""
    return JsonResponse({"status": "ok"})


def readyz(request: HttpRequest) -> JsonResponse:
    """Readiness: database and Redis must both be reachable."""
    checks: dict[str, str] = {}
    ok = True

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure as not-ready
        checks["database"] = f"error: {exc.__class__.__name__}"
        ok = False

    try:
        import redis

        client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
        client.ping()
        client.close()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc.__class__.__name__}"
        ok = False

    return JsonResponse(
        {"status": "ok" if ok else "unavailable", "checks": checks},
        status=200 if ok else 503,
    )
