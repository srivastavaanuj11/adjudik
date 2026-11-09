"""Parsing and validating a single model reply (SPEC §7.6).

Validation is done by hand (not via a JSON Schema validator) so every rejection
maps to one of the fixed error codes. Messages never quote input or output values.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

__all__ = ["ParsedAttempt", "parse_attempt", "ERROR_CODES"]

ERROR_CODES = frozenset(
    {
        "refusal",
        "truncated",
        "invalid_json",
        "outcome_not_allowed",
        "confidence_missing",
        "confidence_out_of_range",
        "rationale_missing",
        "unexpected_field",
    }
)

MAX_RATIONALE_CHARS = 1200

_FENCE = re.compile(r"\A\s*```(?:json)?\s*\n(.*?)\n?\s*```\s*\Z", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ParsedAttempt:
    valid: bool
    outcome: str | None
    confidence: float | None
    rationale: str | None
    rationale_truncated: bool
    error_codes: list[str]
    parsed_output: dict[str, Any] | None


def _reject_constant(value: str) -> float:
    raise ValueError(f"disallowed JSON constant {value}")


def _strip_fence(text: str) -> str:
    match = _FENCE.match(text)
    return match.group(1) if match else text


def parse_attempt(
    *,
    text: str,
    stop_reason: str,
    outcome_ids: list[str],
    want_confidence: bool,
) -> ParsedAttempt:
    """Parse one reply against the local output contract (SPEC §7.6)."""
    if stop_reason == "refusal":
        return _fail(["refusal"])
    if stop_reason == "max_tokens":
        return _fail(["truncated"])

    try:
        data = json.loads(_strip_fence(text), parse_constant=_reject_constant)
    except ValueError:
        return _fail(["invalid_json"])
    if not isinstance(data, dict):
        return _fail(["invalid_json"])

    errors: list[str] = []
    allowed = {"rationale", "outcome"} | ({"confidence"} if want_confidence else set())
    if any(key not in allowed for key in data):
        errors.append("unexpected_field")

    rationale, rationale_truncated = _parse_rationale(data, errors)
    outcome = _parse_outcome(data, outcome_ids, errors)
    confidence = _parse_confidence(data, want_confidence, errors)

    return ParsedAttempt(
        valid=not errors,
        outcome=outcome,
        confidence=confidence,
        rationale=rationale,
        rationale_truncated=rationale_truncated,
        error_codes=errors,
        parsed_output=data,
    )


def _parse_rationale(data: dict[str, Any], errors: list[str]) -> tuple[str | None, bool]:
    rationale = data.get("rationale")
    if not isinstance(rationale, str):
        errors.append("rationale_missing")
        return None, False
    if len(rationale) > MAX_RATIONALE_CHARS:
        return rationale[:MAX_RATIONALE_CHARS], True
    return rationale, False


def _parse_outcome(data: dict[str, Any], outcome_ids: list[str], errors: list[str]) -> str | None:
    value = data.get("outcome")
    if isinstance(value, str):
        trimmed = value.strip().lower()
        for oid in outcome_ids:
            if oid.lower() == trimmed:
                return oid
    errors.append("outcome_not_allowed")
    return None


def _parse_confidence(
    data: dict[str, Any], want_confidence: bool, errors: list[str]
) -> float | None:
    if not want_confidence:
        return None
    if "confidence" not in data:
        errors.append("confidence_missing")
        return None
    value = data["confidence"]
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not (0.0 <= value <= 1.0)
    ):
        errors.append("confidence_out_of_range")
        return None
    return float(value)


def _fail(codes: list[str]) -> ParsedAttempt:
    return ParsedAttempt(
        valid=False,
        outcome=None,
        confidence=None,
        rationale=None,
        rationale_truncated=False,
        error_codes=codes,
        parsed_output=None,
    )
