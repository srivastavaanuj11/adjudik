"""LLM provider registry and implementations (SPEC §7.1, §7.2).

M0 stub. ``build_providers`` returns the configured provider map (falling back to
the SPEC §7.2 defaults, including the keyless ``demo`` provider) from M2 onward.
"""

from __future__ import annotations


def build_providers(spec: str | None = None) -> dict[str, object]:
    """Build the provider map from ``ADJUDIK_PROVIDERS`` (SPEC §7.2).

    Implemented in M2.
    """
    raise NotImplementedError("adjudik_core.providers.build_providers is not implemented until M2")


__all__ = ["build_providers"]
