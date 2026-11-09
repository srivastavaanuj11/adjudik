"""Definition validation (SPEC §5.7). ``validate_definition`` never raises.

Produces a list of :class:`Issue`, each with a JSON Pointer ``path``, a ``code``,
a ``message`` and a ``severity`` (``error`` | ``warning``). Structural checks come
from the Pydantic models (§5.1-§5.6); the cross-reference and schema rules
(§5.7 rules 2-10, not 11) are implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jsonschema
from pydantic import ValidationError

from ..rules.parser import (
    AnyCell,
    Cell,
    CellParseError,
    Comparison,
    Interval,
    Literal,
    NullTest,
    Positive,
    parse_cell,
)
from .models import Definition
from .schema_paths import UnsupportedColumnSchema, column_type_and_nullable, resolve_path_schema

__all__ = ["Issue", "validate_definition"]

_FORBIDDEN_SCHEMA_KEYWORDS = ("pattern", "patternProperties", "$dynamicRef")


@dataclass(frozen=True)
class Issue:
    path: str  # JSON Pointer
    code: str
    message: str
    severity: str = "error"  # "error" | "warning"

    @property
    def is_error(self) -> bool:
        return self.severity == "error"


def _pointer(loc: tuple[Any, ...]) -> str:
    if not loc:
        return ""
    parts = []
    for p in loc:
        s = str(p).replace("~", "~0").replace("/", "~1")
        parts.append(s)
    return "/" + "/".join(parts)


_PYDANTIC_CODE = {
    "missing": "required",
    "extra_forbidden": "unexpected_field",
    "string_pattern_mismatch": "pattern_mismatch",
    "string_too_short": "too_short",
    "string_too_long": "too_long",
    "too_short": "too_short",
    "too_long": "too_long",
    "greater_than_equal": "out_of_range",
    "less_than_equal": "out_of_range",
    "greater_than": "out_of_range",
    "less_than": "out_of_range",
    "literal_error": "invalid_value",
    "value_error": "invalid_value",
}


def _structural_issues(exc: ValidationError) -> list[Issue]:
    issues: list[Issue] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        code = _PYDANTIC_CODE.get(err.get("type", ""), "invalid")
        issues.append(Issue(_pointer(loc), code, err.get("msg", "invalid"), "error"))
    return issues


def validate_definition(doc: Any) -> list[Issue]:
    """Validate a raw definition document and return all issues (SPEC §5.7)."""
    issues: list[Issue] = []

    if not isinstance(doc, dict):
        return [Issue("", "not_an_object", "definition must be a mapping/object", "error")]

    # Rule 2 can run on the raw input_schema even if the rest is malformed.
    issues.extend(_input_schema_issues(doc.get("input_schema")))

    defn: Definition | None = None
    try:
        defn = Definition.model_validate(doc)
    except ValidationError as exc:
        issues.extend(_structural_issues(exc))

    if defn is not None:
        issues.extend(_semantic_issues(defn))

    return issues


# --- Rule 2: input_schema validity (§5.7 rule 2, §5.6) ---


def _input_schema_issues(schema: Any) -> list[Issue]:
    issues: list[Issue] = []
    if schema is None:
        return issues  # "required" is reported structurally
    if not isinstance(schema, dict):
        return [Issue("/input_schema", "input_schema_invalid", "input_schema must be an object")]
    if schema.get("type") != "object":
        issues.append(
            Issue("/input_schema/type", "input_schema_root_not_object", "root type must be object")
        )
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.exceptions.SchemaError as exc:
        issues.append(
            Issue("/input_schema", "input_schema_invalid", f"invalid JSON Schema: {exc.message}")
        )

    _walk_forbidden(schema, "/input_schema", issues)
    return issues


def _walk_forbidden(node: Any, pointer: str, issues: list[Issue]) -> None:
    if isinstance(node, dict):
        for kw in _FORBIDDEN_SCHEMA_KEYWORDS:
            if kw in node:
                issues.append(
                    Issue(
                        f"{pointer}/{kw}",
                        "input_schema_forbidden_keyword",
                        f"`{kw}` is not allowed in input_schema (SPEC §5.6)",
                    )
                )
        if "$ref" in node:
            ref = node["$ref"]
            if not (isinstance(ref, str) and ref.startswith("#/")):
                issues.append(
                    Issue(
                        f"{pointer}/$ref",
                        "input_schema_forbidden_keyword",
                        "only local #/$defs refs are allowed (SPEC §5.6)",
                    )
                )
        for k, v in node.items():
            key = str(k).replace("~", "~0").replace("/", "~1")
            _walk_forbidden(v, f"{pointer}/{key}", issues)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_forbidden(v, f"{pointer}/{i}", issues)


# --- Rules 3-10 on the built model ---


def _semantic_issues(defn: Definition) -> list[Issue]:
    issues: list[Issue] = []
    schema = defn.input_schema
    outcome_ids = {o.id for o in defn.outcomes}

    # Column type/nullability, resolved once (Rule 3 for rule inputs).
    col_type: dict[str, str] = {}
    col_nullable: dict[str, bool] = {}
    if defn.rules is not None:
        for i, inp in enumerate(defn.rules.inputs):
            res = resolve_path_schema(schema, inp.expr)
            ptr = f"/rules/inputs/{i}/expr"
            if not res.found or res.leaf is None:
                issues.append(
                    Issue(
                        ptr,
                        "unresolved_path",
                        f"expr {inp.expr!r} does not resolve through properties",
                    )
                )
                continue
            try:
                ctype, nullable = column_type_and_nullable(res.leaf, res.optional_on_path)
            except UnsupportedColumnSchema as exc:
                issues.append(Issue(ptr, "unsupported_column_schema", str(exc)))
                continue
            col_type[inp.id] = ctype
            col_nullable[inp.id] = nullable

        _rule_body_issues(defn, outcome_ids, col_type, col_nullable, issues)

    # Rule 3 for subject_key and include_fields paths.
    for i, path in enumerate(defn.routing.subject_key):
        if not resolve_path_schema(schema, path).found:
            issues.append(
                Issue(f"/routing/subject_key/{i}", "unresolved_path", f"{path!r} does not resolve")
            )
    if defn.model_step is not None:
        for i, path in enumerate(defn.model_step.include_fields):
            if not resolve_path_schema(schema, path).found:
                issues.append(
                    Issue(
                        f"/model_step/include_fields/{i}",
                        "unresolved_path",
                        f"{path!r} does not resolve",
                    )
                )

    # Rule 6: always_review_outcomes declared.
    for i, oid in enumerate(defn.routing.always_review_outcomes):
        if oid not in outcome_ids:
            issues.append(
                Issue(
                    f"/routing/always_review_outcomes/{i}",
                    "unknown_outcome",
                    f"outcome {oid!r} is not declared",
                )
            )

    # Rule 7: model_step presence.
    needs_model = (
        defn.rules is None
        or defn.rules.on_no_match == "model"
        or (defn.rules is not None and any(r.then.action == "model" for r in defn.rules.rows))
    )
    if needs_model and defn.model_step is None:
        issues.append(
            Issue(
                "/model_step",
                "model_step_required",
                "model_step is required when a path can reach the model (SPEC §5.7 rule 7)",
            )
        )

    return issues


def _rule_body_issues(
    defn: Definition,
    outcome_ids: set[str],
    col_type: dict[str, str],
    col_nullable: dict[str, bool],
    issues: list[Issue],
) -> None:
    assert defn.rules is not None
    rules = defn.rules
    declared_cols = {inp.id for inp in rules.inputs}

    # Rule 8: unique column ids and row ids.
    _dup_ids(((i, inp.id) for i, inp in enumerate(rules.inputs)), "/rules/inputs", issues)
    _dup_ids(((i, row.id) for i, row in enumerate(rules.rows)), "/rules/rows", issues)

    parsed: dict[tuple[int, str], Cell] = {}
    for ri, row in enumerate(rules.rows):
        # Rule 6: decide outcome declared.
        if row.then.action == "decide" and row.then.outcome not in outcome_ids:
            issues.append(
                Issue(
                    f"/rules/rows/{ri}/then/outcome",
                    "unknown_outcome",
                    f"outcome {row.then.outcome!r} is not declared",
                )
            )
        for col_id, cell_str in row.when.items():
            ptr = f"/rules/rows/{ri}/when/{col_id}"
            # Rule 5: when key is a declared column.
            if col_id not in declared_cols:
                issues.append(Issue(ptr, "unknown_column", f"{col_id!r} is not a declared column"))
                continue
            # Rule 4: parse + type compatibility.
            try:
                cell = parse_cell(cell_str)
            except CellParseError as exc:
                issues.append(Issue(ptr, "cell_parse_error", str(exc)))
                continue
            parsed[(ri, col_id)] = cell
            if col_id in col_type:  # only when the column resolved
                for code, msg, severity in _cell_type_issues(
                    cell, col_type[col_id], col_nullable[col_id]
                ):
                    issues.append(Issue(ptr, code, msg, severity))

    # Rule 9 (warning): under FIRST, a row after an all-`-` (always-matching) row is unreachable.
    if rules.hit_policy == "FIRST":
        seen_catch_all = False
        for ri, row in enumerate(rules.rows):
            if seen_catch_all:
                issues.append(
                    Issue(
                        f"/rules/rows/{ri}",
                        "unreachable_row",
                        "row can never match: a preceding FIRST row matches everything",
                        "warning",
                    )
                )
            if _is_catch_all(row):
                seen_catch_all = True


def _is_catch_all(row: Any) -> bool:
    # All declared cells are "-" (or no cells declared) -> matches everything.
    if not row.when:
        return True
    try:
        return all(isinstance(parse_cell(c), AnyCell) for c in row.when.values())
    except CellParseError:
        return False


def _cell_type_issues(cell: Cell, col_type: str, nullable: bool) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    items: tuple[Positive, ...]
    if isinstance(cell, AnyCell):
        return out
    items = cell.items  # MatchList or Negation
    for item in items:
        if isinstance(item, NullTest):
            if not nullable:
                out.append(
                    ("null_on_non_nullable", "`null` cell on a non-nullable column", "error")
                )
            continue
        if isinstance(item, Interval):
            if col_type != "number":
                out.append(
                    ("cell_type_mismatch", "interval is only valid on a number column", "error")
                )
            elif item.low > item.high:
                out.append(("interval_low_gt_high", "interval low must be <= high", "error"))
            continue
        if isinstance(item, Literal):
            if item.kind != col_type:
                out.append(
                    ("cell_type_mismatch", f"{item.kind} literal on a {col_type} column", "error")
                )
            continue
        if isinstance(item, Comparison):
            if item.op in ("<", "<=", ">", ">="):
                if col_type != "number":
                    out.append(
                        (
                            "cell_type_mismatch",
                            "ordering comparison is only valid on a number column",
                            "error",
                        )
                    )
            else:  # = or !=
                if item.kind != col_type:
                    out.append(
                        ("cell_type_mismatch", f"{item.kind} value on a {col_type} column", "error")
                    )
                # Rule 10 (warning): `!=` on a nullable column.
                if item.op == "!=" and nullable:
                    out.append(
                        (
                            "ne_on_nullable_column",
                            "`!=` is false for null on a nullable column",
                            "warning",
                        )
                    )
    return out


def _dup_ids(pairs: Any, base_pointer: str, issues: list[Issue]) -> None:
    seen: dict[str, int] = {}
    for i, ident in pairs:
        if ident in seen:
            issues.append(
                Issue(f"{base_pointer}/{i}/id", "duplicate_id", f"id {ident!r} is not unique")
            )
        else:
            seen[ident] = i
