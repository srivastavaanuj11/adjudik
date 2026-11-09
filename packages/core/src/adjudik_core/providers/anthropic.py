"""Anthropic Messages-API provider (SPEC §7.2).

Uses the official ``anthropic`` SDK (an optional dependency) with structured
output via ``output_config.format``. The SDK and client are imported and built
lazily so a workspace that only runs the ``demo`` provider needs neither the SDK
nor an API key. Tested with mocked HTTP only.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from .base import ModelRequest, ModelResponse, ProviderError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from anthropic import Anthropic

__all__ = ["AnthropicProvider"]

_STOP_REASONS = {"end_turn": "end", "max_tokens": "max_tokens", "refusal": "refusal"}


class AnthropicProvider:
    def __init__(
        self,
        *,
        name: str = "anthropic",
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str | None = None,
        client: Anthropic | None = None,
    ) -> None:
        self.name = name
        self._api_key_env = api_key_env
        self._base_url = base_url
        self._client = client
        self.config_fingerprint: dict[str, Any] = {
            "type": "anthropic",
            "base_url": base_url,
            "deployment": None,
        }

    def _get_client(self) -> Anthropic:
        if self._client is not None:
            return self._client
        try:
            from anthropic import Anthropic
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ProviderError(
                "the 'anthropic' package is required for the anthropic provider",
                code="bad_request",
            ) from exc
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            raise ProviderError(
                f"environment variable {self._api_key_env} is not set",
                code="auth",
            )
        kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 0}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        self._client = Anthropic(**kwargs)
        return self._client

    def complete(self, request: ModelRequest) -> ModelResponse:
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": request.model,
            "system": request.system,
            "messages": [{"role": "user", "content": request.user}],
            "max_tokens": request.max_output_tokens,
            "timeout": request.timeout_s,
            "output_config": {"format": {"type": "json_schema", "schema": request.output_schema}},
        }
        if request.temperature is not None:
            # Sent through extra_body so the parameter always reaches the API and
            # a provider that rejects it fails the step rather than being dropped
            # silently (SPEC §7.8).
            kwargs["extra_body"] = {"temperature": request.temperature}

        start = time.monotonic()
        try:
            message = client.messages.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a typed ProviderError below
            raise _map_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        text = "".join(
            getattr(block, "text", "")
            for block in message.content
            if getattr(block, "type", None) == "text"
        )
        usage = message.usage
        return ModelResponse(
            text=text,
            stop_reason=_STOP_REASONS.get(message.stop_reason or "", "other"),
            model_reported=message.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            latency_ms=latency_ms,
            request_id=getattr(message, "_request_id", None),
        )


def _map_error(exc: Exception) -> ProviderError:
    import anthropic

    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError(str(exc), code="timeout")
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError(str(exc), code="rate_limited", retry_after_s=_retry_after(exc))
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError(str(exc), code="auth")
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError(str(exc), code="not_found")
    if isinstance(exc, anthropic.BadRequestError):
        return ProviderError(str(exc), code="bad_request")
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError(str(exc), code="connection")
    if isinstance(exc, anthropic.APIStatusError):
        status = getattr(exc, "status_code", 500)
        if status >= 500:
            return ProviderError(str(exc), code="server_error")
        return ProviderError(str(exc), code="bad_request")
    if isinstance(exc, ProviderError):  # pragma: no cover - already typed
        return exc
    return ProviderError(str(exc), code="server_error")


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    value = headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):  # pragma: no cover - header format
        return None
