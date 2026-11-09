"""Pydantic models for a decision definition, schema version 1 (SPEC §5.1-§5.5).

These models enforce structure, types, patterns and ranges (SPEC §5.7 rule 1).
Cross-reference and schema-resolution rules (§5.7 rules 2-10) live in
``validate.py`` so they can produce JSON Pointer issues without raising.

``input_schema`` is kept as a raw dict here; it is validated as a JSON Schema
separately (§5.7 rule 2, §5.6).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# Shared regex patterns (SPEC §5.1-§5.5).
KEY_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
ID_PATTERN = r"^[a-z][a-z0-9_]{0,31}$"  # outcomes, rule inputs, reason codes
ROW_ID_PATTERN = r"^[a-z0-9_]{1,64}$"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def normalize_cell(value: Any) -> str:
    """Normalise a raw ``when`` cell value to its FEEL-lite cell string (SPEC §5.2, §1.1).

    - strings are used as written;
    - booleans become ``true`` / ``false``;
    - a null becomes the cell ``null``;
    - numbers are formatted as plain decimals without exponents.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    raise ValueError(f"unsupported cell value of type {type(value).__name__}")


class Outcome(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)


class RuleInput(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    expr: str = Field(min_length=1)


class RuleThen(_Strict):
    action: Literal["decide", "review", "model"]
    outcome: str | None = None

    @model_validator(mode="after")
    def _outcome_presence(self) -> RuleThen:
        if self.action == "decide" and self.outcome is None:
            raise ValueError("`outcome` is required when action is `decide`")
        if self.action != "decide" and self.outcome is not None:
            raise ValueError("`outcome` is forbidden unless action is `decide`")
        return self


class RuleRow(_Strict):
    id: str = Field(pattern=ROW_ID_PATTERN)
    when: dict[str, str] = Field(default_factory=dict)
    then: RuleThen
    note: str | None = Field(default=None, max_length=500)

    @field_validator("when", mode="before")
    @classmethod
    def _normalize_when(cls, v: Any) -> Any:
        if not isinstance(v, dict):
            return v
        return {str(k): normalize_cell(val) for k, val in v.items()}


class Rules(_Strict):
    hit_policy: Literal["FIRST", "UNIQUE", "ANY"] = "FIRST"
    on_no_match: Literal["model", "review"] = "model"
    inputs: list[RuleInput] = Field(min_length=1, max_length=30)
    rows: list[RuleRow] = Field(min_length=1, max_length=500)


class Confidence(_Strict):
    method: Literal["verbalized", "self_consistency"] = "verbalized"
    samples: int = Field(default=5, ge=3, le=10)
    sample_temperature: float = Field(default=1.0, ge=0.1, le=2)


class ModelStep(_Strict):
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_output_tokens: int = Field(default=600, ge=64, le=4096)
    timeout_s: float = Field(default=20, ge=1, le=60)
    max_retries: int = Field(default=2, ge=0, le=3)
    max_repair_attempts: int = Field(default=1, ge=0, le=2)
    include_fields: list[str] = Field(min_length=1, max_length=100)
    policy: str = Field(min_length=1, max_length=20000)
    injection_screen: bool = True
    confidence: Confidence = Field(default_factory=Confidence)


class Routing(_Strict):
    always_review_outcomes: list[str] = Field(default_factory=list)
    subject_key: list[str] = Field(default_factory=list)
    resubmission_window_days: int = Field(default=30, ge=0, le=365)


class ReasonCode(_Strict):
    id: str = Field(pattern=ID_PATTERN)
    label: str = Field(min_length=1)


class Review(_Strict):
    sla_minutes: int = Field(default=240, ge=5, le=43200)
    claim_lease_minutes: int = Field(default=15, ge=2, le=120)
    reason_codes: list[ReasonCode] = Field(default_factory=list, max_length=30)


class Definition(_Strict):
    schema_version: Literal["1"]
    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    input_schema: dict[str, Any]
    outcomes: list[Outcome] = Field(min_length=2, max_length=20)
    rules: Rules | None = None
    model_step: ModelStep | None = None
    routing: Routing = Field(default_factory=Routing)
    review: Review = Field(default_factory=Review)

    @field_validator("description", mode="before")
    @classmethod
    def _none_description_is_empty(cls, v: Any) -> Any:
        return "" if v is None else v
