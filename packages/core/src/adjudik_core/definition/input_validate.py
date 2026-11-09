"""Input validation against a definition's ``input_schema`` with §5.6 caps.

Caps are enforced *before* schema validation (SPEC §5.6, §1.1): strings at 20,000
chars, arrays at 1,000 items, nesting depth at 10, integers within ±(2^53 − 1).
A breach raises :class:`InputValidationError` with code ``string_too_long``,
``array_too_long``, ``too_deep`` or ``integer_out_of_range``; a schema violation
raises it with code ``input_invalid``. The server maps all of these to 422.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema

__all__ = ["InputIssue", "InputValidationError", "validate_input"]

MAX_STRING = 20_000
MAX_ARRAY = 1_000
MAX_DEPTH = 10
INT_LIMIT = 2**53 - 1


@dataclass(frozen=True)
class InputIssue:
    path: str  # JSON Pointer
    code: str
    message: str


class InputValidationError(ValueError):
    def __init__(self, issues: list[InputIssue]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.code}" for i in issues) or "input invalid")


def _cap_issues(data: Any, pointer: str, depth: int) -> list[InputIssue]:
    issues: list[InputIssue] = []
    if depth > MAX_DEPTH:
        return [InputIssue(pointer, "too_deep", f"nesting exceeds {MAX_DEPTH} levels")]
    if isinstance(data, str):
        if len(data) > MAX_STRING:
            issues.append(
                InputIssue(pointer, "string_too_long", f"string exceeds {MAX_STRING} chars")
            )
    elif isinstance(data, bool):
        pass
    elif isinstance(data, int):
        if abs(data) > INT_LIMIT:
            issues.append(InputIssue(pointer, "integer_out_of_range", "integer beyond ±(2^53 − 1)"))
    elif isinstance(data, list):
        if len(data) > MAX_ARRAY:
            issues.append(InputIssue(pointer, "array_too_long", f"array exceeds {MAX_ARRAY} items"))
        for i, v in enumerate(data):
            issues.extend(_cap_issues(v, f"{pointer}/{i}", depth + 1))
    elif isinstance(data, dict):
        for k, v in data.items():
            key = str(k).replace("~", "~0").replace("/", "~1")
            issues.extend(_cap_issues(v, f"{pointer}/{key}", depth + 1))
    return issues


def validate_input(input_schema: dict[str, Any], data: Any) -> Any:
    """Validate ``data`` against ``input_schema`` with §5.6 caps enforced first.

    Returns ``data`` unchanged on success; raises :class:`InputValidationError`.
    """
    cap_issues = _cap_issues(data, "", 1)
    if cap_issues:
        raise InputValidationError(cap_issues)

    validator = jsonschema.Draft202012Validator(input_schema)
    schema_issues = [
        InputIssue(_error_pointer(err), "input_invalid", err.message)
        for err in sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    ]
    if schema_issues:
        raise InputValidationError(schema_issues)
    return data


def _error_pointer(err: jsonschema.exceptions.ValidationError) -> str:
    parts = [str(p).replace("~", "~0").replace("/", "~1") for p in err.absolute_path]
    return "/" + "/".join(parts) if parts else ""
