"""Model-step result and call-record data types (SPEC §7.10)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["CallRecord", "ModelStepResult"]


@dataclass
class CallRecord:
    """One provider request within a step (SPEC §7.10)."""

    id: str
    purpose: str  # "primary" | "repair" | "sample"
    sample_index: int | None
    attempt: int  # 0 for the first attempt of a sequence, then repairs 1, 2
    provider: str
    model_requested: str
    request_hash: str
    system: str
    user: str
    model_reported: str | None = None
    response_text: str | None = None
    parsed_output: dict[str, Any] | None = None
    valid: bool = False
    error_codes: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    provider_error_code: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class ModelStepResult:
    """The outcome of the model step (SPEC §7.10)."""

    ok: bool
    outcome: str | None
    raw_confidence: float | None
    rationale: str | None
    rationale_truncated: bool
    valid_samples: int | None
    model_reported: str | None
    failure_reason: str | None  # SPEC §7.10 failure reasons, or None when ok
    aborted: bool
    calls: list[CallRecord]
