"""Waiting for an evaluation result (SPEC §11.4).

Every transition out of ``processing`` publishes to ``adjudik:eval:{id}`` on
commit. A waiter re-reads the status, then blocks on a Redis subscription in
slices of up to 2 s, re-reading after each slice, until a final status or the
deadline. Per-process and per-key caps bound how many requests may wait at once;
when full, the caller responds at once with the current state.
"""

from __future__ import annotations

import contextlib
import threading
import time
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import connection

if TYPE_CHECKING:  # pragma: no cover
    from .models import Evaluation

CHANNEL_PREFIX = "adjudik:eval:"
SLICE_SECONDS = 2.0
FINAL_STATUSES = frozenset({"decided", "cancelled", "pending_review"})

_waiters_lock = threading.Lock()
_waiters_total = 0
_waiters_per_key: dict[str, int] = {}


def publish_update(evaluation_id) -> None:
    """Publish a wake-up for an evaluation (called on commit, SPEC §11.4)."""
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL)
        client.publish(f"{CHANNEL_PREFIX}{evaluation_id}", "1")
        client.close()
    except Exception:  # pragma: no cover - a failed wake-up only delays a waiter
        pass


def _reserve(api_key_id: str | None) -> bool:
    global _waiters_total
    with _waiters_lock:
        if _waiters_total >= settings.ADJUDIK_MAX_WAITERS_PER_PROCESS:
            return False
        if api_key_id is not None and _waiters_per_key.get(api_key_id, 0) >= 16:
            return False
        _waiters_total += 1
        if api_key_id is not None:
            _waiters_per_key[api_key_id] = _waiters_per_key.get(api_key_id, 0) + 1
        return True


def _release(api_key_id: str | None) -> None:
    global _waiters_total
    with _waiters_lock:
        _waiters_total = max(0, _waiters_total - 1)
        if api_key_id is not None and api_key_id in _waiters_per_key:
            _waiters_per_key[api_key_id] = max(0, _waiters_per_key[api_key_id] - 1)


def _current_status(evaluation_id) -> str:
    from .models import Evaluation

    return Evaluation.objects.values_list("status", flat=True).get(pk=evaluation_id)


def wait_for_result(evaluation: Evaluation, wait_ms: int, api_key_id: str | None) -> str:
    """Block until the evaluation leaves ``processing`` or the deadline (SPEC §11.4).

    Returns the final status. Holds no database connection while waiting.
    """
    status = evaluation.status
    if status != "processing" or wait_ms <= 0:
        return status
    if not _reserve(api_key_id):
        return status  # at capacity: respond with the current state

    # Hold no connection during the wait (SPEC §11.4). Never inside an open
    # transaction (e.g. a test's atomic wrapper), where closing is illegal.
    if not connection.in_atomic_block:
        connection.close()
    deadline = time.monotonic() + wait_ms / 1000.0
    pubsub = None
    try:
        import redis

        client = redis.from_url(settings.REDIS_URL)
        pubsub = client.pubsub()
        pubsub.subscribe(f"{CHANNEL_PREFIX}{evaluation.id}")
        while True:
            status = _current_status(evaluation.id)
            if status != "processing":
                return status
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return status
            pubsub.get_message(timeout=min(SLICE_SECONDS, remaining))
    except Exception:  # pragma: no cover - degrade to a final DB read
        return _current_status(evaluation.id)
    finally:
        if pubsub is not None:
            with contextlib.suppress(Exception):  # pragma: no cover
                pubsub.close()
        _release(api_key_id)
