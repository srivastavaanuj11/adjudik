"""Logging canary and redaction (SPEC §15.5).

Inputs and free text must never reach the logs. ``CanaryFilter`` is attached to
the root handler: if a configured canary token ever appears in a log record it is
redacted in place and the leak is recorded, so a test can plant a canary in an
input and assert nothing logged it. In production the canary is unset and the
filter is a cheap no-op.
"""

from __future__ import annotations

import logging

from django.conf import settings

_LEAKS: list[str] = []
REDACTED = "[REDACTED]"


def reset_leaks() -> None:
    _LEAKS.clear()


def leaks() -> list[str]:
    return list(_LEAKS)


class CanaryFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        canary = getattr(settings, "ADJUDIK_LOG_CANARY", "") or ""
        if not canary:
            return True
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001 - never let logging raise
            return True
        if canary in message:
            _LEAKS.append(message)
            record.msg = message.replace(canary, REDACTED)
            record.args = ()
        return True
