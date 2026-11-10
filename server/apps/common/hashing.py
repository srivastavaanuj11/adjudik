"""Content and lookup hashes (SPEC §9.2).

- Content hashes in audit payloads (``input_hash``, ``rationale_hash`` …) are
  ``HMAC-SHA256(evaluation.salt, canonical(value))``: purging the salt breaks the
  link to a guessed input while the chain still verifies.
- Lookup values that must survive a purge (``request_hmac`` for idempotency,
  ``subject_hmac`` for resubmission) use the per-workspace key instead.
"""

from __future__ import annotations

import os
from typing import Any

from adjudik_core.canonical import salted_hash

SALT_BYTES = 32
WORKSPACE_KEY_BYTES = 32


def new_salt() -> bytes:
    """32 random bytes for one evaluation (SPEC §9.2)."""
    return os.urandom(SALT_BYTES)


def new_workspace_key() -> bytes:
    """32 random bytes for a workspace's lookup HMAC key."""
    return os.urandom(WORKSPACE_KEY_BYTES)


def content_hash(salt: bytes, value: Any) -> str:
    """Salted content hash for audit payloads (SPEC §9.2)."""
    return salted_hash(salt, value)


def lookup_hmac(workspace_key: bytes, value: Any) -> str:
    """Workspace-keyed HMAC for purge-surviving lookups (SPEC §9.2)."""
    return salted_hash(workspace_key, value)


def normalise_request(input_data: Any, external_id: str | None, metadata: Any) -> dict[str, Any]:
    """Normalised request for ``request_hmac`` (SPEC §11.3 step 1).

    Integral floats are converted to ints so ``120.0`` and ``120`` collide.
    """
    return {
        "input": _ints_for_integral_floats(input_data),
        "external_id": external_id,
        "metadata": _ints_for_integral_floats(metadata),
    }


def _ints_for_integral_floats(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _ints_for_integral_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_ints_for_integral_floats(v) for v in value]
    return value
