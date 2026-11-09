"""The committed JSON Schema must match the models (SPEC §17 M1: CI fails if stale)."""

from __future__ import annotations

from pathlib import Path

from adjudik_core.definition.schema_export import schema_json

SCHEMA_PATH = Path(__file__).resolve().parents[3] / "schemas" / "decision-definition.schema.json"


def test_committed_schema_is_current() -> None:
    committed = SCHEMA_PATH.read_text()
    assert committed == schema_json(), (
        "schemas/decision-definition.schema.json is stale; regenerate with "
        "`python -m adjudik_core.definition.schema_export schemas/decision-definition.schema.json`"
    )
