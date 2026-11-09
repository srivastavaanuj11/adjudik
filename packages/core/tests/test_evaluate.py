"""Hit policies, conflicts, nullability and the trace (SPEC §6.1, §6.4, §6.5)."""

from __future__ import annotations

from adjudik_core import load_definition
from adjudik_core.rules.evaluate import (
    CompiledColumn,
    CompiledRow,
    CompiledRuleTable,
    evaluate_rules,
)
from adjudik_core.rules.parser import parse_cell


def _col(id_: str, type_: str = "number", nullable: bool = False) -> CompiledColumn:
    return CompiledColumn(id=id_, expr=id_, type=type_, nullable=nullable)


def _row(id_: str, action: str, outcome: str | None, **cells: str) -> CompiledRow:
    return CompiledRow(
        id=id_,
        action=action,
        outcome=outcome,
        cells={k: parse_cell(v) for k, v in cells.items()},
        note=None,
    )


def _table(hit_policy: str, *rows: CompiledRow, on_no_match: str = "model") -> CompiledRuleTable:
    return CompiledRuleTable(
        hit_policy=hit_policy, on_no_match=on_no_match, columns=(_col("a"), _col("b")), rows=rows
    )


def test_first_picks_first_match() -> None:
    t = _table(
        "FIRST",
        _row("r1", "decide", "x", a="> 0"),
        _row("r2", "decide", "y", a="> 0"),
    )
    res, _ = evaluate_rules(t, {"a": 5, "b": 0})
    assert res.type == "decide" and res.row_id == "r1" and res.outcome == "x"
    assert res.matched_row_ids == ["r1", "r2"]


def test_no_match_returns_no_match() -> None:
    t = _table("FIRST", _row("r1", "decide", "x", a="> 100"))
    res, trace = evaluate_rules(t, {"a": 5, "b": 0})
    assert res.type == "no_match" and res.row_id is None and res.outcome is None
    assert trace["rows"] == [{"id": "r1", "matched": False}]


def test_unique_two_matches_is_conflict() -> None:
    t = _table(
        "UNIQUE",
        _row("r1", "decide", "x", a="> 0"),
        _row("r2", "review", None, b="= 0"),
    )
    res, _ = evaluate_rules(t, {"a": 5, "b": 0})
    assert res.type == "conflict" and res.matched_row_ids == ["r1", "r2"]


def test_unique_single_match_ok() -> None:
    t = _table("UNIQUE", _row("r1", "review", None, a="> 0"))
    res, _ = evaluate_rules(t, {"a": 5, "b": 9})
    assert res.type == "review" and res.row_id == "r1"


def test_any_same_action_outcome_is_not_conflict() -> None:
    t = _table(
        "ANY",
        _row("r1", "decide", "x", a="> 0"),
        _row("r2", "decide", "x", b="= 0"),
    )
    res, _ = evaluate_rules(t, {"a": 5, "b": 0})
    assert res.type == "decide" and res.outcome == "x"
    assert res.matched_row_ids == ["r1", "r2"]


def test_any_differing_outcome_is_conflict() -> None:
    t = _table(
        "ANY",
        _row("r1", "decide", "x", a="> 0"),
        _row("r2", "decide", "y", b="= 0"),
    )
    res, _ = evaluate_rules(t, {"a": 5, "b": 0})
    assert res.type == "conflict"


def test_nullable_column_null_handling() -> None:
    t = _table("FIRST", _row("r1", "decide", "x", a="> 30"))
    # a is null -> comparison false -> no_match.
    res, _ = evaluate_rules(t, {"a": None, "b": 0})
    assert res.type == "no_match"


def test_trace_matches_spec_worked_example() -> None:
    # SPEC §6.5: the refund worked example yields no_match with this exact trace.
    compiled = load_definition("examples/refund_approval/decision.yaml")
    case = {
        "order": {
            "id": "A-1009",
            "amount": 120.0,
            "currency": "USD",
            "status": "delivered",
            "days_since_delivery": 4,
        },
        "customer": {"id": "C", "tier": "standard", "refunds_last_90d": 0},
        "request": {"reason_code": "not_as_described", "reason_text": "x"},
    }
    _res, trace = compiled.evaluate_rules(case)
    assert trace == {
        "hit_policy": "FIRST",
        "columns": {
            "status": "delivered",
            "days": 4,
            "amount": 120.0,
            "tier": "standard",
            "prior_refunds": 0,
            "reason": "not_as_described",
        },
        "rows": [
            {"id": "cancelled_order", "matched": False},
            {"id": "outside_window", "matched": False},
            {"id": "high_value", "matched": False},
            {"id": "frequent_refunder", "matched": False},
            {"id": "small_gold_damaged", "matched": False},
        ],
        "result": {"type": "no_match", "matched_row_ids": [], "row_id": None, "outcome": None},
    }
