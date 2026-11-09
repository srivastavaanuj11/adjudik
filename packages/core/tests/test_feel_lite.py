"""FEEL-lite parsing and evaluation (SPEC §6.2, §6.3).

Covers every golden parse string, a print/re-parse round trip, and Hypothesis
property tests for interval semantics and ``not`` as complement.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from adjudik_core.rules.evaluate import cell_matches
from adjudik_core.rules.parser import (
    AnyCell,
    CellParseError,
    Comparison,
    Interval,
    Literal,
    Negation,
    NullTest,
    parse_cell,
)

GOLDEN_PARSE = [
    "-",
    "null",
    "5",
    "-5",
    "1.5",
    "true",
    '"gold"',
    "> 30",
    "<= 50",
    "!= 5",
    '= "x"',
    '"damaged", "wrong_item"',
    "[1..10]",
    "(1..10]",
    "]1..10[",
    "[1..10)",
    "[-5..-1]",
    "not(5)",
    'not("a", "b")',
    "not([1..5), null)",
]
GOLDEN_REJECT = ["1e5", "+5", "5.", ".5", "not(-)", "[1..10", "= null"]


@pytest.mark.parametrize("text", GOLDEN_PARSE)
def test_golden_parses(text: str) -> None:
    assert parse_cell(text) is not None


@pytest.mark.parametrize("text", GOLDEN_REJECT)
def test_golden_rejects(text: str) -> None:
    with pytest.raises(CellParseError):
        parse_cell(text)


def _val_src(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    return json.dumps(value)


def _to_source(cell: object) -> str:
    if isinstance(cell, AnyCell):
        return "-"
    items = cell.items  # type: ignore[union-attr]
    body = ", ".join(_positive_src(i) for i in items)
    return f"not({body})" if isinstance(cell, Negation) else body


def _positive_src(p: object) -> str:
    if isinstance(p, NullTest):
        return "null"
    if isinstance(p, Literal):
        return _val_src(p.value)
    if isinstance(p, Comparison):
        return f"{p.op} {_val_src(p.value)}"
    if isinstance(p, Interval):
        lo = "[" if p.low_inclusive else "("
        hi = "]" if p.high_inclusive else ")"
        return f"{lo}{format(p.low, 'f')}..{format(p.high, 'f')}{hi}"
    raise AssertionError(p)


@pytest.mark.parametrize("text", GOLDEN_PARSE)
def test_print_reparse_roundtrip(text: str) -> None:
    ast = parse_cell(text)
    assert parse_cell(_to_source(ast)) == ast


# --- Semantics (SPEC §6.3) ---


def test_any_matches_everything_including_null() -> None:
    cell = parse_cell("-")
    for v in (None, 5, "x", True):
        assert cell_matches(cell, v)


def test_null_cell_only_matches_null() -> None:
    cell = parse_cell("null")
    assert cell_matches(cell, None)
    assert not cell_matches(cell, 5)


def test_tests_are_false_for_null_except_null_and_not() -> None:
    assert not cell_matches(parse_cell("!= 5"), None)  # SPEC §6.3
    assert not cell_matches(parse_cell("5"), None)
    assert not cell_matches(parse_cell("> 3"), None)
    assert cell_matches(parse_cell("not(5)"), None)  # true for null


def test_string_equality_exact_case_sensitive() -> None:
    assert cell_matches(parse_cell('"gold"'), "gold")
    assert not cell_matches(parse_cell('"gold"'), "Gold")


def test_numeric_comparisons_use_decimal() -> None:
    assert cell_matches(parse_cell("<= 50"), 50)
    assert cell_matches(parse_cell("<= 50"), 49.999)
    assert not cell_matches(parse_cell("<= 50"), 50.0001)
    assert cell_matches(parse_cell("= 5"), 5.0)


def test_list_is_or() -> None:
    cell = parse_cell('"damaged", "wrong_item"')
    assert cell_matches(cell, "damaged")
    assert cell_matches(cell, "wrong_item")
    assert not cell_matches(cell, "changed_mind")


@given(
    low=st.integers(-50, 50),
    high=st.integers(-50, 50),
    lo_inc=st.booleans(),
    hi_inc=st.booleans(),
    value=st.integers(-60, 60),
)
def test_interval_semantics_match_reference(low, high, lo_inc, hi_inc, value) -> None:
    if low > high:
        low, high = high, low
    lo = "[" if lo_inc else "("
    hi = "]" if hi_inc else ")"
    cell = parse_cell(f"{lo}{low}..{high}{hi}")
    ref_low = value >= low if lo_inc else value > low
    ref_high = value <= high if hi_inc else value < high
    assert cell_matches(cell, value) == (ref_low and ref_high)


@given(
    members=st.lists(st.integers(-20, 20), min_size=1, max_size=5, unique=True),
    value=st.integers(-25, 25),
)
def test_not_is_complement_for_non_null(members, value) -> None:
    body = ", ".join(str(m) for m in members)
    positive = parse_cell(body)
    negated = parse_cell(f"not({body})")
    assert cell_matches(negated, value) == (not cell_matches(positive, value))
