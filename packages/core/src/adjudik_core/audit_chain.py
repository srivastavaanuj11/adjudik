"""Audit hash chain verification and signed checkpoints (SPEC §9.2, §9.5, §9.6).

Framework-free helpers the server and the ``adjudik audit verify`` CLI use to
recompute a per-workspace hash chain and to sign and check hourly checkpoints.

- :func:`verify` recomputes every event's chain hash from a starting seq and
  previous hash, checking the chain is gapless and links correctly.
- Checkpoints are an Ed25519 signature over the canonical JSON of
  ``{workspace_id, seq, hash, created_at, key_id}``; :func:`sign_checkpoint`
  produces one and :func:`verify_checkpoint` checks it.
- :func:`generate_signing_key`, :func:`key_id` and the load helpers manage the
  base64 Ed25519 key material in ``ADJUDIK_AUDIT_SIGNING_KEY`` (SPEC §9.5).

The module imports no Django/DRF/Celery.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from .canonical import ZERO_HASH, canonical_bytes, chain_hash

__all__ = [
    "ZERO_HASH",
    "VerifyResult",
    "CheckpointError",
    "verify",
    "chain_fields",
    "generate_signing_key",
    "load_private_key",
    "load_public_key",
    "public_key_b64",
    "key_id",
    "canonical_checkpoint",
    "sign_checkpoint",
    "verify_checkpoint",
]

# The event fields that go into the chain hash, in SPEC §9.2 order.
_CHAIN_FIELDS = (
    "seq",
    "workspace_id",
    "event_type",
    "occurred_at",
    "actor_type",
    "actor_id",
    "evaluation_id",
    "subject_type",
    "subject_id",
    "payload",
)


class CheckpointError(ValueError):
    """A checkpoint cannot be signed or its key material is malformed."""


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of recomputing a chain segment (SPEC §9.6)."""

    ok: bool
    head_seq: int
    head_hash: str
    first_bad_seq: int | None
    mismatch_count: int


def chain_fields(event: Mapping[str, Any]) -> dict[str, Any]:
    """Project an event record to exactly the hashed fields (SPEC §9.2)."""
    return {field: event[field] for field in _CHAIN_FIELDS}


def verify(
    events_iter: Iterable[Mapping[str, Any]],
    start_seq: int,
    start_prev_hash: str,
) -> VerifyResult:
    """Recompute hashes over ``events_iter`` (SPEC §9.6).

    ``events_iter`` yields event records in ascending ``seq``; each must carry
    the §9.2 chain fields plus the stored ``hash`` and ``prev_hash``. The first
    event must have ``seq == start_seq`` and ``prev_hash == start_prev_hash``
    (``start_prev_hash`` is :data:`ZERO_HASH` when starting from seq 1).

    Returns the first seq whose stored hash or link is wrong, if any, and the
    head reached. Verification does not stop at the first bad seq: it keeps
    following the *stored* ``prev_hash`` so the head and a total mismatch count
    are reported, which the reconcile job records.
    """
    expected_seq = start_seq
    prev_hash = start_prev_hash
    head_seq = start_seq - 1
    head_hash = start_prev_hash
    first_bad_seq: int | None = None
    mismatch_count = 0
    seen_any = False

    for event in events_iter:
        seen_any = True
        seq = int(event["seq"])
        stored_hash = str(event["hash"])
        stored_prev = str(event["prev_hash"])

        bad = False
        if seq != expected_seq:
            bad = True  # gap or duplicate
        if stored_prev != prev_hash:
            bad = True  # broken link
        recomputed = chain_hash(stored_prev, chain_fields(event))
        if recomputed != stored_hash:
            bad = True  # tampered content

        if bad:
            mismatch_count += 1
            if first_bad_seq is None:
                first_bad_seq = seq

        head_seq = seq
        head_hash = stored_hash
        prev_hash = stored_hash
        expected_seq = seq + 1

    if not seen_any:
        return VerifyResult(
            ok=True,
            head_seq=start_seq - 1,
            head_hash=start_prev_hash,
            first_bad_seq=None,
            mismatch_count=0,
        )

    return VerifyResult(
        ok=first_bad_seq is None,
        head_seq=head_seq,
        head_hash=head_hash,
        first_bad_seq=first_bad_seq,
        mismatch_count=mismatch_count,
    )


# --- Ed25519 checkpoint signing (SPEC §9.5) ---


def generate_signing_key() -> tuple[str, str]:
    """Return a fresh ``(private_b64, public_b64)`` Ed25519 key pair.

    Both are standard base64 of the raw 32-byte key, as stored in
    ``ADJUDIK_AUDIT_SIGNING_KEY`` and published by ``GET /audit/public-key``.
    """
    private = Ed25519PrivateKey.generate()
    raw_priv = private.private_bytes_raw()
    raw_pub = private.public_key().public_bytes_raw()
    return base64.b64encode(raw_priv).decode("ascii"), base64.b64encode(raw_pub).decode("ascii")


def load_private_key(private_b64: str) -> Ed25519PrivateKey:
    """Load an Ed25519 private key from its base64 raw form."""
    try:
        raw = base64.b64decode(private_b64, validate=True)
        return Ed25519PrivateKey.from_private_bytes(raw)
    except Exception as exc:  # noqa: BLE001 - normalise to one typed error
        raise CheckpointError(f"invalid audit signing key: {exc.__class__.__name__}") from exc


def load_public_key(public_b64: str) -> Ed25519PublicKey:
    """Load an Ed25519 public key from its base64 raw form."""
    try:
        raw = base64.b64decode(public_b64, validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except Exception as exc:  # noqa: BLE001 - normalise to one typed error
        raise CheckpointError(f"invalid audit public key: {exc.__class__.__name__}") from exc


def public_key_b64(private_b64: str) -> str:
    """Return the base64 public key matching a base64 private key."""
    private = load_private_key(private_b64)
    raw_pub = private.public_key().public_bytes_raw()
    return base64.b64encode(raw_pub).decode("ascii")


def key_id(public_b64: str) -> str:
    """First 16 hex chars of SHA-256 of the raw public key (SPEC §9.5)."""
    raw = base64.b64decode(public_b64, validate=True)
    return hashlib.sha256(raw).hexdigest()[:16]


def canonical_checkpoint(checkpoint: Mapping[str, Any]) -> bytes:
    """Canonical JSON over the signed checkpoint fields (SPEC §9.5)."""
    body = {
        "workspace_id": checkpoint["workspace_id"],
        "seq": checkpoint["seq"],
        "hash": checkpoint["hash"],
        "created_at": checkpoint["created_at"],
        "key_id": checkpoint["key_id"],
    }
    return canonical_bytes(body)


def sign_checkpoint(checkpoint: Mapping[str, Any], private_b64: str) -> str:
    """Return the hex Ed25519 signature over a checkpoint's canonical JSON."""
    private = load_private_key(private_b64)
    signature = private.sign(canonical_checkpoint(checkpoint))
    return signature.hex()


def verify_checkpoint(checkpoint: Mapping[str, Any], public_b64: str) -> bool:
    """Check a checkpoint's Ed25519 signature (SPEC §9.6)."""
    try:
        public = load_public_key(public_b64)
        public.verify(bytes.fromhex(str(checkpoint["signature"])), canonical_checkpoint(checkpoint))
        return True
    except (InvalidSignature, ValueError, CheckpointError):
        return False
