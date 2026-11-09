"""Definition hashes (SPEC §5.8). SHA-256 hex over JCS canonical JSON (§9.2).

- ``definition_hash``: the whole definition after defaults are filled in.
- ``rules_hash``: the ``rules`` section after defaults, excluding row ``note``s
  (SPEC §1.1), or ``None`` when there are no rules.
"""

from __future__ import annotations

from ..canonical import sha256_hex
from .models import Definition

__all__ = ["definition_hash", "rules_hash"]


def definition_hash(defn: Definition) -> str:
    return sha256_hex(defn.model_dump(mode="json"))


def rules_hash(defn: Definition) -> str | None:
    if defn.rules is None:
        return None
    rules = defn.rules.model_dump(mode="json")
    for row in rules.get("rows", []):
        row.pop("note", None)  # notes do not affect the rules hash (SPEC §1.1)
    return sha256_hex(rules)
