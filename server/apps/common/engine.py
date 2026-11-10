"""Engine and provider registry built from Django settings (SPEC §4.5, §7.2).

The server builds one provider registry from ``ADJUDIK_PROVIDERS`` and hands the
core :class:`Engine` a ``SystemRandom`` by default (SPEC §8.2); tests inject a
seeded ``random.Random`` and a ``FixedClock``. The registry is cached for the
process since it is derived only from configuration.
"""

from __future__ import annotations

import random
from functools import lru_cache
from typing import TYPE_CHECKING

from django.conf import settings

from adjudik_core import Engine
from adjudik_core.providers import build_providers

if TYPE_CHECKING:  # pragma: no cover
    from adjudik_core import Clock
    from adjudik_core.providers import ModelProvider


@lru_cache(maxsize=1)
def get_providers() -> dict[str, ModelProvider]:
    """Build the provider registry from ``ADJUDIK_PROVIDERS`` (SPEC §7.2)."""
    return build_providers(settings.ADJUDIK_PROVIDERS or None)


def provider_names() -> set[str]:
    return set(get_providers())


def build_engine(*, rng: random.Random | None = None, clock: Clock | None = None) -> Engine:
    """An :class:`Engine` over the configured providers.

    Production draws with ``random.SystemRandom``; tests pass a seeded
    ``random.Random`` (SPEC §8.2).
    """
    return Engine(
        providers=get_providers(),
        rng=rng if rng is not None else random.SystemRandom(),
        clock=clock,
    )
