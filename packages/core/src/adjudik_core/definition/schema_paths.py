"""Resolve dotted paths through an ``input_schema`` (SPEC §6.1, §5.7 rule 3).

Used to derive rule-column types and nullability, and to check that every
``subject_key`` / ``include_fields`` path resolves. Only local ``#/$defs/...``
refs are followed (SPEC §5.7 rule 3); non-local refs are rejected upstream (§5.6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "PathResolution",
    "UnsupportedColumnSchema",
    "resolve_path_schema",
    "column_type_and_nullable",
    "deref",
]

SCALAR_TYPES = {"string", "number", "integer", "boolean"}


class UnsupportedColumnSchema(ValueError):
    """A rule-input leaf is not a single scalar type (optionally unioned with null)."""


@dataclass(frozen=True)
class PathResolution:
    found: bool
    leaf: dict[str, Any] | None
    optional_on_path: bool  # any property on the path is not in its parent's `required`


def _deref(schema: Any, root: dict[str, Any]) -> Any:
    """Follow local ``#/$defs/...`` refs to a concrete schema node."""
    seen: set[int] = set()
    while isinstance(schema, dict) and "$ref" in schema:
        ref = schema["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/"):
            return None  # non-local ref: unresolvable here
        node: Any = root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        if id(node) in seen:
            return None
        seen.add(id(node))
        schema = node
    return schema


def deref(schema: Any, root: dict[str, Any]) -> Any:
    """Public wrapper: follow local ``#/$defs/...`` refs to a concrete node."""
    return _deref(schema, root)


def resolve_path_schema(root: dict[str, Any], path: str) -> PathResolution:
    """Resolve ``path`` (e.g. ``order.status``) through ``properties`` (SPEC §6.1)."""
    schema: Any = _deref(root, root)
    optional = False
    for part in path.split("."):
        schema = _deref(schema, root)
        if not isinstance(schema, dict):
            return PathResolution(False, None, optional)
        props = schema.get("properties")
        if not isinstance(props, dict) or part not in props:
            return PathResolution(False, None, optional)
        required = schema.get("required", [])
        if not isinstance(required, list) or part not in required:
            optional = True
        schema = props[part]
    leaf = _deref(schema, root)
    if not isinstance(leaf, dict):
        return PathResolution(False, None, optional)
    return PathResolution(True, leaf, optional)


def column_type_and_nullable(leaf: dict[str, Any], optional_on_path: bool) -> tuple[str, bool]:
    """Derive a rule column's type and nullability from its leaf schema (SPEC §6.1).

    Raises :class:`UnsupportedColumnSchema` if the leaf is not exactly one scalar
    type (optionally unioned with ``null``).
    """
    t = leaf.get("type")
    types = t if isinstance(t, list) else [t]
    has_null = "null" in types
    scalar = [x for x in types if x != "null"]
    if len(scalar) != 1 or scalar[0] not in SCALAR_TYPES:
        raise UnsupportedColumnSchema(
            "rule input leaf must be exactly one of string/number/integer/boolean "
            "(optionally with null)"
        )
    base = scalar[0]
    col_type = "number" if base in {"number", "integer"} else base
    return col_type, (has_null or optional_on_path)
