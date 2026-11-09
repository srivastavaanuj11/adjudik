"""``sample_input``: a placeholder input filling every property (SPEC §4.5, §1.1).

Placeholders: ``"x"`` for strings, ``0`` for numbers, ``false`` for booleans, the
first value of an ``enum``. It respects ``minimum``, ``minLength``, ``const`` and
arrays (``[]``). Used by the dashboard Test tab and prompt preview, and by a test
asserting ``validate_input(sample_input(d))`` passes for both examples.
"""

from __future__ import annotations

from typing import Any

from .schema_paths import _deref

__all__ = ["sample_input"]


def sample_input(input_schema: dict[str, Any]) -> dict[str, Any]:
    root = _deref(input_schema, input_schema)
    result = _sample(root, input_schema)
    return result if isinstance(result, dict) else {}


def _sample(schema: Any, root: dict[str, Any]) -> Any:
    schema = _deref(schema, root)
    if not isinstance(schema, dict):
        return None

    if "const" in schema:
        return schema["const"]
    enum = schema.get("enum")
    if isinstance(enum, list) and enum:
        return enum[0]

    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    base = next((x for x in types if x != "null"), None)

    if base == "object":
        props = schema.get("properties")
        out: dict[str, Any] = {}
        if isinstance(props, dict):
            for name, subschema in props.items():
                out[name] = _sample(subschema, root)
        return out
    if base == "array":
        min_items = schema.get("minItems", 0)
        if isinstance(min_items, int) and min_items > 0:
            item_schema = schema.get("items", {})
            return [_sample(item_schema, root) for _ in range(min_items)]
        return []
    if base == "string":
        min_len = schema.get("minLength", 0)
        return "x" * min_len if isinstance(min_len, int) and min_len > 1 else "x"
    if base in ("number", "integer"):
        minimum = schema.get("minimum")
        if isinstance(minimum, (int, float)) and minimum > 0:
            return minimum
        return 0
    if base == "boolean":
        return False
    return None
