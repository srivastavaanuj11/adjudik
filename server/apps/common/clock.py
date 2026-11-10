"""Injected clock (SPEC §16.4).

Services take a clock so tests control time; evaluation, review and audit
timestamps are never ``auto_now_add``. :func:`now` is the default production
clock (timezone-aware UTC). Tests pass a ``FixedClock`` or a stepping clock.
"""

from __future__ import annotations

import datetime as _dt
from typing import Protocol


class Clock(Protocol):
    def now(self) -> _dt.datetime: ...


class SystemClock:
    """UTC wall clock with microseconds."""

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(tz=_dt.UTC)


class FixedClock:
    """A clock pinned at one instant, optionally advanced by ``tick``."""

    def __init__(self, instant: _dt.datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_dt.UTC)
        self._instant = instant

    def now(self) -> _dt.datetime:
        return self._instant

    def tick(self, seconds: float) -> None:
        self._instant = self._instant + _dt.timedelta(seconds=seconds)

    def set(self, instant: _dt.datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=_dt.UTC)
        self._instant = instant


_default = SystemClock()


def now() -> _dt.datetime:
    return _default.now()


def iso_micros(dt: _dt.datetime) -> str:
    """Render ``dt`` as ``YYYY-MM-DDTHH:MM:SS.ffffffZ`` (SPEC §9.1)."""
    dt = dt.astimezone(_dt.UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
