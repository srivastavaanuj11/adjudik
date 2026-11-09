"""Model providers and their configuration (SPEC §7.1, §7.2).

``build_providers`` reads the ``ADJUDIK_PROVIDERS`` JSON configuration (used by
both server and CLI) into a name-keyed registry. Secrets are never in the
configuration: each entry names an environment variable the provider reads at
call time.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .anthropic import AnthropicProvider
from .base import ModelProvider, ModelRequest, ModelResponse, ProviderError
from .fake import FakeProvider, KeywordFakeProvider
from .openai_compat import OpenAICompatibleProvider

__all__ = [
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "FakeProvider",
    "KeywordFakeProvider",
    "AnthropicProvider",
    "OpenAICompatibleProvider",
    "build_providers",
    "DEFAULT_PROVIDERS_CONFIG",
    "ProviderConfigError",
]

# The default configuration shipped in .env.example (SPEC §7.2).
DEFAULT_PROVIDERS_CONFIG: dict[str, Any] = {
    "anthropic": {"type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY"},
    "demo": {
        "type": "keyword_fake",
        "keywords": {
            "approve": ["damaged", "broken", "wrong", "missing", "defective", "unopened"],
            "deny": ["used", "changed my mind", "no longer need", "works fine"],
            "billing": ["invoice", "charge", "refund", "payment", "billed"],
            "technical": ["error", "crash", "bug", "not loading", "api"],
            "account": ["password", "login", "locked out", "email change", "2fa"],
            "shipping": ["delivery", "tracking", "courier", "parcel", "shipped"],
            "other": ["feedback", "suggestion", "partnership"],
        },
    },
}


class ProviderConfigError(ValueError):
    """``ADJUDIK_PROVIDERS`` is malformed or names an unknown provider type."""


def build_providers(
    config: str | dict[str, Any] | None = None,
) -> dict[str, ModelProvider]:
    """Build the provider registry from configuration (SPEC §7.2).

    ``config`` may be a JSON string, a parsed dict, or ``None`` to read the
    ``ADJUDIK_PROVIDERS`` environment variable, falling back to
    :data:`DEFAULT_PROVIDERS_CONFIG`.
    """
    raw = _load_config(config)
    providers: dict[str, ModelProvider] = {}
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            raise ProviderConfigError(f"provider {name!r} configuration must be an object")
        providers[name] = _build_one(name, entry)
    return providers


def _load_config(config: str | dict[str, Any] | None) -> dict[str, Any]:
    if config is None:
        env = os.environ.get("ADJUDIK_PROVIDERS")
        if env is None or not env.strip():
            return DEFAULT_PROVIDERS_CONFIG
        config = env
    if isinstance(config, str):
        try:
            parsed = json.loads(config)
        except json.JSONDecodeError as exc:
            raise ProviderConfigError(f"ADJUDIK_PROVIDERS is not valid JSON: {exc}") from exc
    else:
        parsed = config
    if not isinstance(parsed, dict):
        raise ProviderConfigError("provider configuration must be a JSON object")
    return parsed


def _build_one(name: str, entry: dict[str, Any]) -> ModelProvider:
    ptype = entry.get("type")
    if ptype == "anthropic":
        return AnthropicProvider(
            name=name,
            api_key_env=entry.get("api_key_env", "ANTHROPIC_API_KEY"),
            base_url=entry.get("base_url"),
        )
    if ptype == "openai_compatible":
        return OpenAICompatibleProvider(
            name=name,
            api_key_env=entry.get("api_key_env", "OPENAI_API_KEY"),
            base_url=entry.get("base_url"),
            deployment=entry.get("deployment"),
            response_format=entry.get("response_format", "json_schema"),
        )
    if ptype == "keyword_fake":
        keywords = entry.get("keywords", {})
        if not isinstance(keywords, dict):
            raise ProviderConfigError(f"provider {name!r} keyword_fake needs a keywords map")
        return KeywordFakeProvider(keywords=keywords, name=name)
    raise ProviderConfigError(f"provider {name!r} has unknown type {ptype!r}")
