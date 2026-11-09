"""OpenAI-compatible Chat-Completions provider (SPEC §7.2).

Works against OpenAI, Azure OpenAI, vLLM or Ollama via a configurable
``base_url`` and ``deployment``. Structured output uses ``response_format`` mode
``json_schema`` (strict, default), ``json_object`` or ``none``; local validation
always runs regardless. Official ``openai`` SDK, imported lazily. Tested with
mocked HTTP only.
"""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any

from .base import ModelRequest, ModelResponse, ProviderError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from openai import OpenAI

__all__ = ["OpenAICompatibleProvider"]

_FINISH_REASONS = {"stop": "end", "length": "max_tokens", "content_filter": "refusal"}
_RESPONSE_FORMATS = {"json_schema", "json_object", "none"}


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        name: str = "openai_compatible",
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str | None = None,
        deployment: str | None = None,
        response_format: str = "json_schema",
        client: OpenAI | None = None,
    ) -> None:
        if response_format not in _RESPONSE_FORMATS:
            raise ValueError(f"unknown response_format {response_format!r}")
        self.name = name
        self._api_key_env = api_key_env
        self._base_url = base_url
        self._deployment = deployment
        self._response_format = response_format
        self._client = client
        self.config_fingerprint: dict[str, Any] = {
            "type": "openai_compatible",
            "base_url": base_url,
            "deployment": deployment,
        }

    def _get_client(self) -> OpenAI:
        if self._client is not None:
            return self._client
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ProviderError(
                "the 'openai' package is required for the openai_compatible provider",
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
        self._client = OpenAI(**kwargs)
        return self._client

    def complete(self, request: ModelRequest) -> ModelResponse:
        client = self._get_client()
        model = self._deployment or request.model
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "max_tokens": request.max_output_tokens,
            "timeout": request.timeout_s,
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        response_format = self._build_response_format(request.output_schema)
        if response_format is not None:
            kwargs["response_format"] = response_format

        start = time.monotonic()
        try:
            completion = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001 - mapped to a typed ProviderError below
            raise _map_error(exc) from exc
        latency_ms = int((time.monotonic() - start) * 1000)

        choice = completion.choices[0]
        usage = completion.usage
        return ModelResponse(
            text=choice.message.content or "",
            stop_reason=_FINISH_REASONS.get(choice.finish_reason or "", "other"),
            model_reported=completion.model,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            latency_ms=latency_ms,
            request_id=getattr(completion, "_request_id", None),
        )

    def _build_response_format(self, output_schema: dict[str, Any]) -> dict[str, Any] | None:
        if self._response_format == "none":
            return None
        if self._response_format == "json_object":
            return {"type": "json_object"}
        return {
            "type": "json_schema",
            "json_schema": {"name": "decision", "schema": output_schema, "strict": True},
        }


def _map_error(exc: Exception) -> ProviderError:
    import openai

    if isinstance(exc, openai.APITimeoutError):
        return ProviderError(str(exc), code="timeout")
    if isinstance(exc, openai.RateLimitError):
        return ProviderError(str(exc), code="rate_limited", retry_after_s=_retry_after(exc))
    if isinstance(exc, openai.AuthenticationError):
        return ProviderError(str(exc), code="auth")
    if isinstance(exc, openai.NotFoundError):
        return ProviderError(str(exc), code="not_found")
    if isinstance(exc, openai.BadRequestError):
        return ProviderError(str(exc), code="bad_request")
    if isinstance(exc, openai.APIConnectionError):
        return ProviderError(str(exc), code="connection")
    if isinstance(exc, openai.APIStatusError):
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
