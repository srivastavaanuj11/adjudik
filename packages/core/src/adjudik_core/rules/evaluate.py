"""FEEL-lite evaluation, hit policies and the rule trace (SPEC §6.3-§6.5).

Compiled rule tables (built by the definition loader) are evaluated here against a
validated input. Every row is evaluated so the trace is complete; the hit policy
then selects the result. No ``eval``/``exec`` is ever used.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .parser import (
    AnyCell,
    Cell,
    Comparison,
    Interval,
    Literal,
    MatchList,
    Negation,
    NullTest,
    Positive,
)

__all__ = [
    "CompiledColumn",
    "CompiledRow",
    "CompiledRuleTable",
    "RuleResult",
    "resolve_path",
    "cell_matches",
    "evaluate_rules",
]


@dataclass(frozen=True)
class CompiledColumn:
    id: str
    expr: str
    type: str  # "number" | "string" | "boolean"
    nullable: bool


@dataclass(frozen=True)
class CompiledRow:
    id: str
    action: str  # "decide" | "review" | "model"
    outcome: str | None
    cells: dict[str, Cell]  # column id -> parsed cell; columns omitted mean "-"
    note: str | None


@dataclass(frozen=True)
class CompiledRuleTable:
    hit_policy: str  # "FIRST" | "UNIQUE" | "ANY"
    on_no_match: str  # "model" | "review"
    columns: tuple[CompiledColumn, ...]
    rows: tuple[CompiledRow, ...]


@dataclass(frozen=True)
class RuleResult:
    type: str  # "decide" | "review" | "model" | "no_match" | "conflict"
    matched_row_ids: list[str]
    row_id: str | None
    outcome: str | None


def resolve_path(data: Any, expr: str) -> Any:
    """Resolve a dotted path against the input; missing keys resolve to ``None`` (SPEC §6.1)."""
    current = data
    for part in expr.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _is_number(value: Any) -> bool:
    # bool is a subclass of int; exclude it from "number".
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _as_decimal(value: Any) -> Decimal:
    return Decimal(str(value))


def _positive_matches(test: Positive, value: Any) -> bool:
    if isinstance(test, NullTest):
        return value is None
    # Every test except `-`, `null` and `not(...)` is false when the input is null.
    if value is None:
        return False
    if isinstance(test, Literal):
        return _equals(test.kind, test.value, value)
    if isinstance(test, Comparison):
        op = test.op
        if op == "=":
            return _equals(test.kind, test.value, value)
        if op == "!=":
            return not _equals(test.kind, test.value, value)
        # Numeric ordering comparisons: the grammar guarantees a Decimal here.
        if not _is_number(value) or not isinstance(test.value, Decimal):
            return False
        left = _as_decimal(value)
        right = test.value
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        return False
    if isinstance(test, Interval):
        if not _is_number(value):
            return False
        v = _as_decimal(value)
        low_ok = v >= test.low if test.low_inclusive else v > test.low
        high_ok = v <= test.high if test.high_inclusive else v < test.high
        return low_ok and high_ok
    return False  # pragma: no cover - exhaustive above


def _equals(kind: str, literal: Any, value: Any) -> bool:
    if kind == "number":
        return _is_number(value) and _as_decimal(value) == literal
    if kind == "string":
        return isinstance(value, str) and value == literal
    if kind == "boolean":
        return isinstance(value, bool) and value == literal
    return False


def cell_matches(cell: Cell, value: Any) -> bool:
    """Whether a parsed cell matches a resolved input value (SPEC §6.3)."""
    if isinstance(cell, AnyCell):
        return True
    if isinstance(cell, MatchList):
        return any(_positive_matches(item, value) for item in cell.items)
    if isinstance(cell, Negation):
        return not any(_positive_matches(item, value) for item in cell.items)
    return False  # pragma: no cover


def _row_matches(row: CompiledRow, values: dict[str, Any]) -> bool:
    # A column omitted from `when` means `-` (always matches), so only declared
    # cells are checked; the row matches when all of them match (AND).
    return all(cell_matches(cell, values[col_id]) for col_id, cell in row.cells.items())


def evaluate_rules(table: CompiledRuleTable, data: Any) -> tuple[RuleResult, dict[str, Any]]:
    """Evaluate a compiled rule table and produce (RuleResult, trace) (SPEC §6.4-§6.5)."""
    values = {col.id: resolve_path(data, col.expr) for col in table.columns}
    row_matched = [(row, _row_matches(row, values)) for row in table.rows]
    matched = [row for row, ok in row_matched if ok]
    matched_ids = [row.id for row in matched]

    result = _select(table.hit_policy, matched, matched_ids)

    trace = {
        "hit_policy": table.hit_policy,
        "columns": values,
        "rows": [{"id": row.id, "matched": ok} for row, ok in row_matched],
        "result": {
            "type": result.type,
            "matched_row_ids": result.matched_row_ids,
            "row_id": result.row_id,
            "outcome": result.outcome,
        },
    }
    return result, trace


def _result_type_for(row: CompiledRow) -> str:
    # action -> rule result type; "decide" carries the outcome.
    return row.action


def _select(hit_policy: str, matched: list[CompiledRow], matched_ids: list[str]) -> RuleResult:
    if not matched:
        return RuleResult(type="no_match", matched_row_ids=[], row_id=None, outcome=None)

    if hit_policy == "FIRST":
        row = matched[0]
        return RuleResult(_result_type_for(row), matched_ids, row.id, row.outcome)

    if hit_policy == "UNIQUE":
        if len(matched) == 1:
            row = matched[0]
            return RuleResult(_result_type_for(row), matched_ids, row.id, row.outcome)
        return RuleResult("conflict", matched_ids, None, None)

    if hit_policy == "ANY":
        if len(matched) == 1:
            row = matched[0]
            return RuleResult(_result_type_for(row), matched_ids, row.id, row.outcome)
        actions = {(row.action, row.outcome) for row in matched}
        if len(actions) == 1:
            row = matched[0]
            return RuleResult(_result_type_for(row), matched_ids, row.id, row.outcome)
        return RuleResult("conflict", matched_ids, None, None)

    raise ValueError(f"unknown hit policy {hit_policy!r}")  # pragma: no cover
