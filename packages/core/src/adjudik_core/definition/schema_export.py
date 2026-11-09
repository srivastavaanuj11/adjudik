"""Generate the committed JSON Schema for a decision definition (SPEC §17 M1).

``schemas/decision-definition.schema.json`` is generated from the Pydantic models
and committed; a test (and CI) fail if the committed file is stale. Run
``python -m adjudik_core.definition.schema_export PATH`` to regenerate it.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from .models import Definition

__all__ = ["decision_definition_schema", "schema_json"]


def decision_definition_schema() -> dict[str, Any]:
    schema = Definition.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Adjudik decision definition (schema version 1)"
    return schema


def schema_json() -> str:
    """Deterministic, sorted JSON text with a trailing newline."""
    return json.dumps(decision_definition_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    target = sys.argv[1] if len(sys.argv) > 1 else "schemas/decision-definition.schema.json"
    with open(target, "w") as fh:
        fh.write(schema_json())
    print(f"wrote {target}")


if __name__ == "__main__":
    main()
