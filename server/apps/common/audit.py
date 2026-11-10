"""Audit chain append service (SPEC §9.3).

``append_many`` is the single primitive the server uses to add events. It locks
the workspace's chain head, assigns gapless seqs, computes each chain hash, and
updates the head — all inside the caller's transaction, as the last statement
before commit. Calling it twice in one transaction raises (SPEC §9.3).
"""

from __future__ import annotations

import datetime as _dt
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from django.db import transaction

from adjudik_core.canonical import canonical_bytes, chain_hash
from apps.audit.models import AuditChainHead, AuditEvent

from .clock import iso_micros

SYSTEM_WORKSPACE_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")


class AuditAppendError(RuntimeError):
    """append_many was misused (outside a transaction, or called twice)."""


@dataclass
class Event:
    """One event to append; seq/prev_hash/hash are filled by :func:`append_many`."""

    event_type: str
    occurred_at: _dt.datetime
    actor_type: str = "system"
    actor_id: str | None = None
    evaluation_id: uuid.UUID | str | None = None
    subject_type: str | None = None
    subject_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


def _normalise_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """``json.loads(canonical(payload))`` so storage re-hashes identically (SPEC §9.2)."""
    return json.loads(canonical_bytes(payload))


def _hash_fields(seq: int, workspace_id: str, ev: Event, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "seq": seq,
        "workspace_id": workspace_id,
        "event_type": ev.event_type,
        "occurred_at": iso_micros(ev.occurred_at),
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "evaluation_id": str(ev.evaluation_id) if ev.evaluation_id is not None else None,
        "subject_type": ev.subject_type,
        "subject_id": ev.subject_id,
        "payload": payload,
    }


def _append_guard() -> None:
    """Enforce the one-append-per-transaction rule (SPEC §9.3).

    The marker is keyed to ``(id(connection.run_on_commit), innermost savepoint)``.
    Django swaps ``run_on_commit`` for a fresh list when each transaction commits,
    so sequential transactions on one reused connection — for example a submit and
    the model worker that its on-commit hook runs — get distinct keys, while a
    genuine second append within the same transaction and savepoint repeats the
    key and is rejected.
    """
    conn = transaction.get_connection()
    if not conn.in_atomic_block:
        raise AuditAppendError("append_many must run inside a transaction (SPEC §9.3)")
    savepoint = conn.savepoint_ids[-1] if conn.savepoint_ids else "__outermost__"
    marker = (id(conn.run_on_commit), savepoint)
    if getattr(conn, "_adjudik_append_marker", None) == marker:
        raise AuditAppendError("append_many called twice in one transaction (SPEC §9.3)")
    conn._adjudik_append_marker = marker


def append_many(workspace_id: uuid.UUID | str, events: list[Event]) -> list[AuditEvent]:
    """Append ``events`` to the workspace chain and return the created rows.

    Must be the last write before commit. Locks ``audit_chain_head`` FOR UPDATE,
    assigns sequential seqs from the head, links and hashes each event, bulk
    inserts them, and advances the head.
    """
    _append_guard()
    if not events:
        return []

    ws_str = str(workspace_id)
    head = AuditChainHead.objects.select_for_update().get(workspace_id=workspace_id)
    seq = head.seq
    prev_hash = head.hash

    rows: list[AuditEvent] = []
    for ev in events:
        seq += 1
        payload = _normalise_payload(ev.payload)
        fields = _hash_fields(seq, ws_str, ev, payload)
        h = chain_hash(prev_hash, fields)
        rows.append(
            AuditEvent(
                workspace_id=workspace_id,
                seq=seq,
                event_type=ev.event_type,
                occurred_at=ev.occurred_at,
                actor_type=ev.actor_type,
                actor_id=ev.actor_id,
                evaluation_id=ev.evaluation_id,
                subject_type=ev.subject_type,
                subject_id=ev.subject_id,
                payload=payload,
                prev_hash=prev_hash,
                hash=h,
            )
        )
        prev_hash = h

    AuditEvent.objects.bulk_create(rows)
    head.seq = seq
    head.hash = prev_hash
    head.save(update_fields=["seq", "hash"])
    return rows
