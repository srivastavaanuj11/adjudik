"""Provider interface: the small contract the model step depends on (SPEC §7.1).

Concrete providers live beside this module. They raise :class:`ProviderError` for
transport and configuration problems; they never raise for a model that simply
replied badly — that is the model step's job to parse (§7.6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "ModelRequest",
    "ModelResponse",
    "ProviderError",
    "ModelProvider",
]


@dataclass(frozen=True)
class ModelRequest:
    model: str
    system: str
    user: str
    output_schema: dict[str, Any]  # provider-safe JSON Schema (SPEC §7.6)
    temperature: float | None
    max_output_tokens: int
    timeout_s: float


@dataclass(frozen=True)
class ModelResponse:
    text: str
    stop_reason: str  # "end" | "max_tokens" | "refusal" | "other"
    model_reported: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    request_id: str | None = None


# Error codes a provider may report (SPEC §7.1). ``retryable`` is derived from the
# code so callers and providers agree on which failures back off and retry.
_RETRYABLE_CODES = frozenset({"timeout", "rate_limited", "server_error", "connection"})
_NON_RETRYABLE_CODES = frozenset({"auth", "bad_request", "not_found"})
_ALL_CODES = _RETRYABLE_CODES | _NON_RETRYABLE_CODES


class ProviderError(Exception):
    """A transport or configuration failure from a provider (SPEC §7.1)."""

    def __init__(
        self,
        message: str,
        *,
        code: str,
        retry_after_s: float | None = None,
    ) -> None:
        if code not in _ALL_CODES:
            raise ValueError(f"unknown provider error code {code!r}")
        super().__init__(message)
        self.code = code
        self.retryable = code in _RETRYABLE_CODES
        self.retry_after_s = retry_after_s


@runtime_checkable
class ModelProvider(Protocol):
    name: str
    config_fingerprint: dict[str, Any]  # type, base_url, deployment (never secrets)

    def complete(self, request: ModelRequest) -> ModelResponse: ...


@dataclass
class _BaseProvider:
    """Shared attribute carrier so providers satisfy the protocol cleanly."""

    name: str
    config_fingerprint: dict[str, Any] = field(default_factory=dict)
