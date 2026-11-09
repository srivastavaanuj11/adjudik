"""Data minimisation and untrusted-string discovery (SPEC §7.3).

``minimize_input`` copies only the ``include_fields`` paths from the validated
input into a new object with the same nesting; missing optional fields are left
out. ``iter_untrusted_strings`` walks the minimized object alongside the schema so
the injection screen (§7.4) can scan exactly the strings that are untrusted — a
string leaf is untrusted unless its schema has ``enum`` or ``const``.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..definition.schema_paths import deref

__all__ = ["minimize_input", "iter_untrusted_strings"]


def minimize_input(
    input_schema: dict[str, Any], include_fields: list[str], data: Any
) -> dict[str, Any]:
    """Project ``data`` onto ``include_fields``, preserving nesting (SPEC §7.3)."""
    result: dict[str, Any] = {}
    for path in include_fields:
        parts = path.split(".")
        value, present = _resolve_value(data, parts)
        if not present:
            continue  # missing optional field is left out
        node = result
        for part in parts[:-1]:
            child = node.get(part)
            if not isinstance(child, dict):
                child = {}
                node[part] = child
            node = child
        node[parts[-1]] = value
    return result


def _resolve_value(data: Any, parts: list[str]) -> tuple[Any, bool]:
    current = data
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None, False
    return current, True


def iter_untrusted_strings(
    input_schema: dict[str, Any], minimized: Any
) -> Iterator[tuple[str, str]]:
    """Yield ``(path, value)`` for each untrusted string leaf (SPEC §7.3)."""
    root_schema = deref(input_schema, input_schema)
    yield from _walk(minimized, root_schema, input_schema, "")


def _walk(value: Any, schema: Any, root: dict[str, Any], path: str) -> Iterator[tuple[str, str]]:
    schema = deref(schema, root) if isinstance(schema, dict) else None

    if isinstance(value, dict):
        props = schema.get("properties") if isinstance(schema, dict) else None
        for key, child in value.items():
            child_schema = props.get(key) if isinstance(props, dict) else None
            child_path = f"{path}.{key}" if path else key
            yield from _walk(child, child_schema, root, child_path)
        return

    if isinstance(value, list):
        items = schema.get("items") if isinstance(schema, dict) else None
        for index, child in enumerate(value):
            yield from _walk(child, items, root, f"{path}[{index}]")
        return

    if isinstance(value, str) and not _is_trusted(schema):
        yield path, value


def _is_trusted(schema: Any) -> bool:
    # A string with a fixed vocabulary cannot carry an injection payload.
    return isinstance(schema, dict) and ("enum" in schema or "const" in schema)
