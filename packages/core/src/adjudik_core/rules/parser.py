"""FEEL-lite parser: cell string -> immutable AST (SPEC §6.2).

The grammar is kept verbatim in ``feel_lite.lark`` and parsed with an LALR,
contextual-lexer Lark parser. Cells are parsed once at load time into the AST
below; evaluation (``evaluate.py``) never calls ``eval``/``exec`` (SPEC §6.3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from importlib import resources
from typing import cast

from lark import Lark, Token, Transformer, v_args
from lark.exceptions import LarkError

__all__ = [
    "CellParseError",
    "parse_cell",
    "AnyCell",
    "MatchList",
    "Negation",
    "Comparison",
    "Interval",
    "Literal",
    "NullTest",
    "Positive",
    "Cell",
]

MAX_CELL_CHARS = 1000  # SPEC §5.6
MAX_LIST_ITEMS = 100  # SPEC §5.6


class CellParseError(ValueError):
    """A FEEL-lite cell is not well-formed or exceeds a §5.6 limit."""


# --- AST nodes (frozen / immutable) ---


@dataclass(frozen=True)
class Literal:
    value: Decimal | str | bool
    kind: str  # "number" | "string" | "boolean"


@dataclass(frozen=True)
class NullTest:
    pass


@dataclass(frozen=True)
class Comparison:
    op: str  # "<=" | ">=" | "!=" | "<" | ">" | "="
    value: Decimal | str | bool
    kind: str  # "number" | "string" | "boolean"


@dataclass(frozen=True)
class Interval:
    low: Decimal
    low_inclusive: bool
    high: Decimal
    high_inclusive: bool


Positive = Literal | NullTest | Comparison | Interval


@dataclass(frozen=True)
class AnyCell:
    """The ``-`` cell: matches anything, including null."""


@dataclass(frozen=True)
class MatchList:
    """One or more positive tests; matches if any does (SPEC §6.3)."""

    items: tuple[Positive, ...]


@dataclass(frozen=True)
class Negation:
    """``not(...)``: matches if none of the listed tests does (SPEC §6.3)."""

    items: tuple[Positive, ...]


Cell = AnyCell | MatchList | Negation


_OPEN_INCLUSIVE = {"[": True, "(": False, "]": False}
_CLOSE_INCLUSIVE = {"]": True, ")": False, "[": False}


@v_args(inline=True)
class _ToAst(Transformer[Token, object]):
    def number(self, tok: Token) -> Decimal:
        return Decimal(str(tok))

    def string(self, tok: Token) -> str:
        # ESCAPED_STRING includes the surrounding quotes; decode JSON-style.
        return cast(str, json.loads(str(tok)))

    def boolean(self, tok: Token) -> bool:
        return str(tok) == "true"

    def null(self) -> NullTest:
        return NullTest()

    def comparison(self, cmp_tok: Token, value: Decimal | str | bool) -> Comparison:
        return Comparison(op=str(cmp_tok), value=value, kind=_kind_of(value))

    def open(self, tok: Token) -> str:
        return str(tok)

    def close(self, tok: Token) -> str:
        return str(tok)

    def interval(
        self, open_bracket: str, low: Decimal, high: Decimal, close_bracket: str
    ) -> Interval:
        return Interval(
            low=low,
            low_inclusive=_OPEN_INCLUSIVE[open_bracket],
            high=high,
            high_inclusive=_CLOSE_INCLUSIVE[close_bracket],
        )

    def positive_list(self, *items: object) -> tuple[Positive, ...]:
        return tuple(_as_positive(i) for i in items)

    def any(self) -> Cell:
        return AnyCell()

    def negation(self, items: tuple[Positive, ...]) -> Cell:
        return Negation(items=items)


def _kind_of(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, Decimal):
        return "number"
    if isinstance(value, str):
        return "string"
    raise CellParseError(f"unexpected literal type {type(value).__name__}")


def _as_positive(item: object) -> Positive:
    # Bare literals arrive as Decimal/str/bool; wrap them as Literal nodes.
    if isinstance(item, (Comparison, Interval, NullTest, Literal)):
        return item
    return Literal(value=item, kind=_kind_of(item))  # type: ignore[arg-type]


@lru_cache(maxsize=1)
def _parser() -> Lark:
    grammar = resources.files("adjudik_core.rules").joinpath("feel_lite.lark").read_text()
    return Lark(grammar, parser="lalr", lexer="contextual", start="cell")


@lru_cache(maxsize=4096)
def parse_cell(text: str) -> Cell:
    """Parse a FEEL-lite cell string into its AST (SPEC §6.2).

    Raises :class:`CellParseError` on a syntax error or a §5.6 size breach.
    """
    if len(text) > MAX_CELL_CHARS:
        raise CellParseError(f"cell exceeds {MAX_CELL_CHARS} characters (SPEC §5.6)")
    try:
        tree = _parser().parse(text)
    except LarkError as exc:
        raise CellParseError(f"invalid FEEL-lite cell {text!r}: {exc}") from exc

    result = _ToAst().transform(tree)
    # A positive_list at the top became a tuple; wrap as MatchList.
    if isinstance(result, tuple):
        cell: Cell = MatchList(items=result)
    else:
        cell = cast(Cell, result)  # AnyCell or Negation

    items = getattr(cell, "items", ())
    if len(items) > MAX_LIST_ITEMS:
        raise CellParseError(f"cell has more than {MAX_LIST_ITEMS} list items (SPEC §5.6)")
    return cell
