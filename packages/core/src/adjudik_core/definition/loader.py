"""Loading and compiling decision definitions (SPEC §4.5, §5).

``load_definition`` accepts a path, a YAML/JSON string, or a dict; it validates
(raising :class:`DefinitionError` on any error-severity issue) and returns an
immutable :class:`CompiledDefinition` with pre-parsed rule cells and hashes.
Compiled definitions are cached by ``definition_hash`` (LRU, 128 entries).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..canonical import canonical_bytes
from ..rules.evaluate import (
    CompiledColumn,
    CompiledRow,
    CompiledRuleTable,
    RuleResult,
    evaluate_rules,
)
from ..rules.parser import parse_cell
from .hashing import definition_hash, rules_hash
from .input_validate import validate_input
from .models import Definition
from .schema_paths import column_type_and_nullable, resolve_path_schema
from .validate import Issue, validate_definition

__all__ = ["CompiledDefinition", "DefinitionError", "load_definition", "validate_definition"]


class DefinitionError(ValueError):
    def __init__(self, issues: list[Issue]) -> None:
        self.issues = issues
        errors = [i for i in issues if i.is_error]
        super().__init__(
            f"{len(errors)} definition error(s): " + "; ".join(f"{i.path} {i.code}" for i in errors)
        )


@dataclass(frozen=True)
class CompiledDefinition:
    definition: Definition
    definition_hash: str
    rules_hash: str | None
    rule_table: CompiledRuleTable | None

    @property
    def key(self) -> str:
        return self.definition.key

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.definition.input_schema

    def validate_input(self, data: Any) -> Any:
        return validate_input(self.input_schema, data)

    def evaluate_rules(self, data: Any) -> tuple[RuleResult, dict[str, Any]]:
        if self.rule_table is None:
            # No rules: everything is handed to the model (SPEC §5.2).
            return (
                RuleResult(type="no_match", matched_row_ids=[], row_id=None, outcome=None),
                {
                    "hit_policy": None,
                    "columns": {},
                    "rows": [],
                    "result": {
                        "type": "no_match",
                        "matched_row_ids": [],
                        "row_id": None,
                        "outcome": None,
                    },
                },
            )
        return evaluate_rules(self.rule_table, data)


def _to_doc(source: str | bytes | Path | dict[str, Any]) -> Any:
    from .yaml_loader import load_yaml

    if isinstance(source, dict):
        return source
    if isinstance(source, Path):
        return load_yaml(source.read_text())
    if isinstance(source, bytes):
        return load_yaml(source)
    if isinstance(source, str):
        # A short string that looks like a path and names an existing file is
        # loaded from disk; otherwise it is parsed as YAML/JSON content.
        looks_like_path = len(source) < 4096 and (
            os.path.sep in source or source.endswith((".yaml", ".yml", ".json"))
        )
        if looks_like_path:
            p = Path(source)
            if p.is_file():
                return load_yaml(p.read_text())
        return load_yaml(source)
    raise TypeError(f"unsupported definition source type {type(source).__name__}")


def load_definition(source: str | bytes | Path | dict[str, Any]) -> CompiledDefinition:
    doc = _to_doc(source)
    issues = validate_definition(doc)
    if any(i.is_error for i in issues):
        raise DefinitionError(issues)
    defn = Definition.model_validate(doc)
    canonical = canonical_bytes(defn.model_dump(mode="json")).decode("ascii")
    return _compile_cached(canonical)


@lru_cache(maxsize=128)
def _compile_cached(canonical: str) -> CompiledDefinition:
    import json

    defn = Definition.model_validate(json.loads(canonical))
    return _compile(defn)


def _compile(defn: Definition) -> CompiledDefinition:
    table: CompiledRuleTable | None = None
    if defn.rules is not None:
        columns = []
        for inp in defn.rules.inputs:
            res = resolve_path_schema(defn.input_schema, inp.expr)
            assert res.found and res.leaf is not None  # load_definition validated this
            ctype, nullable = column_type_and_nullable(res.leaf, res.optional_on_path)
            columns.append(CompiledColumn(id=inp.id, expr=inp.expr, type=ctype, nullable=nullable))
        rows = []
        for row in defn.rules.rows:
            cells = {col_id: parse_cell(cell) for col_id, cell in row.when.items()}
            rows.append(
                CompiledRow(
                    id=row.id,
                    action=row.then.action,
                    outcome=row.then.outcome,
                    cells=cells,
                    note=row.note,
                )
            )
        table = CompiledRuleTable(
            hit_policy=defn.rules.hit_policy,
            on_no_match=defn.rules.on_no_match,
            columns=tuple(columns),
            rows=tuple(rows),
        )
    return CompiledDefinition(
        definition=defn,
        definition_hash=definition_hash(defn),
        rules_hash=rules_hash(defn),
        rule_table=table,
    )
