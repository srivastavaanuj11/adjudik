"""Canonical JSON (RFC 8785 / JCS) and the hashing primitives (SPEC §9.2).

All content hashing in Adjudik goes through this module:

- :func:`canonical_bytes` produces the RFC 8785 canonical encoding of a value.
  ``NaN``, ``Infinity`` and ``Decimal`` are rejected (SPEC §9.2).
- :func:`sha256_hex` is the plain SHA-256 hex digest over canonical bytes, used
  for ``definition_hash`` / ``rules_hash`` / fingerprints (SPEC §5.8).
- :func:`salted_hash` is ``HMAC-SHA256(salt, canonical(value))`` for the per-
  evaluation content hashes (SPEC §9.2); purging the salt breaks the link.
- :func:`chain_hash` computes an audit event's chain hash (SPEC §9.2).

The module imports no Django/DRF/Celery.
"""

from __future__ import annotations

import hashlib
import hmac
from decimal import Decimal
from typing import Any

import rfc8785

__all__ = [
    "CanonicalError",
    "canonical_bytes",
    "sha256_hex",
    "salted_hash",
    "chain_hash",
    "ZERO_HASH",
]

# prev_hash for seq 1 (SPEC §9.1): 64 zeros.
ZERO_HASH = "0" * 64


class CanonicalError(ValueError):
    """A value cannot be canonicalised (non-finite float, Decimal, bad type)."""


def _reject_unsupported(value: Any) -> None:
    """Reject Decimal and non-finite floats anywhere in the structure.

    ``rfc8785`` rejects these itself, but we raise a single, typed error with a
    clear message so callers never leak library-specific exceptions.
    """
    if isinstance(value, Decimal):
        raise CanonicalError("Decimal is not allowed in canonical JSON (SPEC §9.2)")
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CanonicalError("NaN/Infinity are not allowed in canonical JSON (SPEC §9.2)")
    elif isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise CanonicalError("object keys must be strings in canonical JSON")
            _reject_unsupported(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _reject_unsupported(v)


def canonical_bytes(value: Any) -> bytes:
    """Return the RFC 8785 canonical JSON encoding of ``value`` as bytes."""
    _reject_unsupported(value)
    try:
        return rfc8785.dumps(value)
    except rfc8785.CanonicalizationError as exc:  # pragma: no cover - guarded above
        raise CanonicalError(str(exc)) from exc


def sha256_hex(value: Any) -> str:
    """SHA-256 hex digest over the canonical JSON of ``value`` (SPEC §5.8)."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def salted_hash(salt: bytes, value: Any) -> str:
    """``HMAC-SHA256(salt, canonical(value))`` hex digest (SPEC §9.2)."""
    return hmac.new(salt, canonical_bytes(value), hashlib.sha256).hexdigest()


def chain_hash(prev_hash: str, event: dict[str, Any]) -> str:
    """Audit chain hash for one event (SPEC §9.2).

    ``event`` holds the hashed fields: ``seq, workspace_id, event_type,
    occurred_at, actor_type, actor_id, evaluation_id, subject_type, subject_id,
    payload``. ``occurred_at`` must already be the ``YYYY-MM-DDTHH:MM:SS.ffffffZ``
    string form.
    """
    body = (prev_hash + "\n").encode("ascii") + canonical_bytes(event)
    return hashlib.sha256(body).hexdigest()
