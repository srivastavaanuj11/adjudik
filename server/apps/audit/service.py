"""Signed checkpoints, verification and NDJSON export (SPEC §9.5, §9.6).

Checkpoints are Ed25519-signed over ``{workspace_id, seq, hash, created_at,
key_id}`` using ``ADJUDIK_AUDIT_SIGNING_KEY``. Verification recomputes the chain
(incremental, full or reconcile) and records the result; export streams the
chain plus its checkpoints and a final checkpoint signed at export time, which
``adjudik audit verify`` uses to detect tampering and truncation.
"""

from __future__ import annotations

import datetime as _dt
from collections.abc import Iterator
from typing import Any

from django.conf import settings
from django.db import transaction

from adjudik_core import audit_chain, load_definition
from adjudik_core.canonical import ZERO_HASH
from apps.common import audit
from apps.common.audit import Event
from apps.common.clock import iso_micros

from .models import (
    AuditChainHead,
    AuditCheckpoint,
    AuditEvent,
    AuditVerification,
    VerificationMode,
    VerificationStatus,
)

BATCH = 10000
MAX_MISMATCH_IDS = 100


class SigningKeyMissing(RuntimeError):
    """No audit signing key is configured (SPEC §9.5)."""


def _private_key_b64() -> str:
    key = settings.ADJUDIK_AUDIT_SIGNING_KEY
    if not key:
        raise SigningKeyMissing("ADJUDIK_AUDIT_SIGNING_KEY is not set")
    return key


def public_key_b64() -> str:
    return audit_chain.public_key_b64(_private_key_b64())


def key_id() -> str:
    return audit_chain.key_id(public_key_b64())


# --- checkpoints (SPEC §9.5) ---


def _checkpoint_payload(workspace_id, seq: int, chain_hash: str, created_at: str, kid: str) -> dict:
    return {
        "workspace_id": str(workspace_id),
        "seq": seq,
        "hash": chain_hash,
        "created_at": created_at,
        "key_id": kid,
    }


def write_checkpoint(workspace_id, now: _dt.datetime) -> AuditCheckpoint | None:
    """Sign and store a checkpoint for a workspace's head, if it has new events."""
    head = AuditChainHead.objects.filter(workspace_id=workspace_id).first()
    if head is None or head.seq == 0:
        return None
    last = AuditCheckpoint.objects.filter(workspace_id=workspace_id).order_by("-seq").first()
    if last is not None and last.seq >= head.seq:
        return None  # nothing new since the last checkpoint

    created_at = iso_micros(now)
    kid = key_id()
    payload = _checkpoint_payload(workspace_id, head.seq, head.hash, created_at, kid)
    signature = audit_chain.sign_checkpoint(payload, _private_key_b64())
    cp = AuditCheckpoint.objects.create(
        workspace_id=workspace_id,
        seq=head.seq,
        hash=head.hash,
        created_at=now,
        key_id=kid,
        signature=signature,
    )
    _anchor(payload, signature)
    return cp


def _anchor(payload: dict, signature: str) -> None:
    """POST the checkpoint to ``ADJUDIK_AUDIT_ANCHOR_URL`` if configured (SPEC §9.5)."""
    url = settings.ADJUDIK_AUDIT_ANCHOR_URL
    if not url:
        return
    import contextlib

    with contextlib.suppress(Exception):  # pragma: no cover - best-effort external copy
        import json
        import urllib.request

        body = json.dumps({**payload, "signature": signature}).encode("utf-8")
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)  # noqa: S310 - operator-provided URL


def write_all_checkpoints(now: _dt.datetime) -> int:
    """Checkpoint every workspace chain with new events; one transaction each."""
    written = 0
    for head in AuditChainHead.objects.filter(seq__gt=0):
        with transaction.atomic():
            if write_checkpoint(head.workspace_id, now) is not None:
                written += 1
    return written


# --- verification (SPEC §9.6) ---


def _event_record(row: AuditEvent) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "workspace_id": str(row.workspace_id),
        "event_type": row.event_type,
        "occurred_at": iso_micros(row.occurred_at),
        "actor_type": row.actor_type,
        "actor_id": row.actor_id,
        "evaluation_id": str(row.evaluation_id) if row.evaluation_id else None,
        "subject_type": row.subject_type,
        "subject_id": row.subject_id,
        "payload": row.payload,
        "prev_hash": row.prev_hash,
        "hash": row.hash,
    }


def _iter_events(workspace_id, start_seq: int) -> Iterator[dict[str, Any]]:
    """Keyset batches of events from ``start_seq`` (SPEC §9.6)."""
    seq = start_seq
    while True:
        rows = list(
            AuditEvent.objects.filter(workspace_id=workspace_id, seq__gte=seq).order_by("seq")[
                :BATCH
            ]
        )
        if not rows:
            return
        for row in rows:
            yield _event_record(row)
        seq = rows[-1].seq + 1
        if len(rows) < BATCH:
            return


def _start_anchor(workspace_id, mode: str) -> tuple[int, str]:
    """Return ``(start_seq, start_prev_hash)`` for a verification mode (SPEC §9.6)."""
    if mode == VerificationMode.INCREMENTAL:
        last = AuditCheckpoint.objects.filter(workspace_id=workspace_id).order_by("-seq").first()
        if last is not None:
            return last.seq + 1, last.hash
    return 1, ZERO_HASH


def _check_checkpoints(workspace_id, start_seq: int) -> list[dict[str, Any]]:
    """Verify each checkpoint's signature and that its hash matches its event."""
    public = public_key_b64()
    bad: list[dict[str, Any]] = []
    checkpoints = AuditCheckpoint.objects.filter(
        workspace_id=workspace_id, seq__gte=start_seq
    ).order_by("seq")
    for cp in checkpoints:
        payload = _checkpoint_payload(
            workspace_id, cp.seq, cp.hash, iso_micros(cp.created_at), cp.key_id
        )
        payload["signature"] = cp.signature
        if not audit_chain.verify_checkpoint(payload, public):
            bad.append({"checkpoint_seq": cp.seq, "reason": "bad_signature"})
            continue
        event = AuditEvent.objects.filter(workspace_id=workspace_id, seq=cp.seq).first()
        if event is None or event.hash != cp.hash:
            bad.append({"checkpoint_seq": cp.seq, "reason": "hash_mismatch"})
    return bad


def _reconcile_checks(workspace_id) -> list[dict[str, Any]]:
    """Fold-based reconciliation extras (SPEC §9.6).

    Recomputes ``definition_hash`` for every published version and ``input_hash``
    for evaluations whose input and salt still exist.
    """
    from apps.common.hashing import content_hash
    from apps.decisions.models import DecisionVersion, VersionStatus
    from apps.evaluations.models import Evaluation

    mismatches: list[dict[str, Any]] = []
    versions = DecisionVersion.objects.filter(
        workspace_id=workspace_id, status__in=[VersionStatus.ACTIVE, VersionStatus.RETIRED]
    )
    for v in versions:
        try:
            recomputed = load_definition(v.definition).definition_hash
        except Exception:  # noqa: BLE001 - a bad stored definition is itself a mismatch
            mismatches.append({"decision_version_id": str(v.id), "reason": "definition_invalid"})
            continue
        if recomputed != v.definition_hash:
            mismatches.append(
                {"decision_version_id": str(v.id), "reason": "definition_hash_mismatch"}
            )
            if len(mismatches) >= MAX_MISMATCH_IDS:
                return mismatches

    evals = (
        Evaluation.objects.filter(workspace_id=workspace_id)
        .exclude(input__isnull=True)
        .exclude(salt__isnull=True)[: MAX_MISMATCH_IDS * 2]
    )
    for e in evals:
        if e.input_hash and content_hash(bytes(e.salt), e.input) != e.input_hash:
            mismatches.append({"evaluation_id": str(e.id), "reason": "input_hash_mismatch"})
            if len(mismatches) >= MAX_MISMATCH_IDS:
                break
    return mismatches


def run_verification(workspace_id, mode: str, *, requested_by: str | None, now: _dt.datetime):
    """Run a verification and record it; append ``audit.verified`` separately."""
    start_seq, start_prev = _start_anchor(workspace_id, mode)
    verification = AuditVerification.objects.create(
        workspace_id=workspace_id,
        mode=mode,
        status=VerificationStatus.RUNNING,
        from_seq=start_seq,
        requested_by=requested_by,
        created_at=now,
    )
    try:
        result = audit_chain.verify(_iter_events(workspace_id, start_seq), start_seq, start_prev)
        mismatches: list[dict[str, Any]] = []
        if result.first_bad_seq is not None:
            mismatches.append({"first_bad_seq": result.first_bad_seq})
        mismatches.extend(_check_checkpoints(workspace_id, start_seq))
        if mode == VerificationMode.RECONCILE:
            mismatches.extend(_reconcile_checks(workspace_id))
        ok = result.ok and not mismatches
        verification.ok = ok
        verification.head_seq = result.head_seq
        verification.first_bad_seq = result.first_bad_seq
        verification.mismatches = mismatches[:MAX_MISMATCH_IDS] or None
        verification.status = VerificationStatus.SUCCEEDED
    except Exception as exc:  # noqa: BLE001 - the job records its own failure
        verification.status = VerificationStatus.FAILED
        verification.ok = False
        verification.error = exc.__class__.__name__
    verification.finished_at = now
    verification.save()

    with transaction.atomic():
        audit.append_many(
            workspace_id,
            [
                Event(
                    "audit.verified",
                    now,
                    actor_type="system" if requested_by is None else "user",
                    actor_id=requested_by,
                    subject_type="audit_verification",
                    subject_id=str(verification.id),
                    payload={
                        "verification_id": str(verification.id),
                        "mode": mode,
                        "ok": bool(verification.ok),
                        "first_bad_seq": verification.first_bad_seq,
                        "head_seq": verification.head_seq,
                        "mismatch_count": len(verification.mismatches or []),
                    },
                )
            ],
        )
    return verification


def verify_all_chains(mode: str, now: _dt.datetime) -> int:
    count = 0
    for head in AuditChainHead.objects.filter(seq__gt=0):
        run_verification(head.workspace_id, mode, requested_by=None, now=now)
        count += 1
    return count


# --- NDJSON export (SPEC §9.6) ---


def export_lines(
    workspace_id, from_seq: int, to_seq: int | None, now: _dt.datetime
) -> Iterator[str]:
    """Yield NDJSON event, checkpoint and trailing-checkpoint lines (SPEC §9.6)."""
    import json

    head = AuditChainHead.objects.filter(workspace_id=workspace_id).first()
    head_seq = head.seq if head else 0
    upper = min(to_seq, head_seq) if to_seq is not None else head_seq

    last_hash = ZERO_HASH
    seq = max(1, from_seq)
    emitted_to = from_seq - 1
    while seq <= upper:
        rows = list(
            AuditEvent.objects.filter(
                workspace_id=workspace_id, seq__gte=seq, seq__lte=upper
            ).order_by("seq")[:BATCH]
        )
        if not rows:
            break
        for row in rows:
            rec = _event_record(row)
            rec["kind"] = "event"
            yield json.dumps(rec) + "\n"
            last_hash = row.hash
            emitted_to = row.seq
        seq = rows[-1].seq + 1

    for cp in AuditCheckpoint.objects.filter(
        workspace_id=workspace_id, seq__gte=from_seq, seq__lte=upper
    ).order_by("seq"):
        yield (
            json.dumps(
                {
                    "kind": "checkpoint",
                    "workspace_id": str(workspace_id),
                    "seq": cp.seq,
                    "hash": cp.hash,
                    "created_at": iso_micros(cp.created_at),
                    "key_id": cp.key_id,
                    "signature": cp.signature,
                }
            )
            + "\n"
        )

    # Final checkpoint signed at export time over the last exported event.
    if emitted_to >= 1:
        created_at = iso_micros(now)
        kid = key_id()
        payload = _checkpoint_payload(workspace_id, emitted_to, last_hash, created_at, kid)
        signature = audit_chain.sign_checkpoint(payload, _private_key_b64())
        yield json.dumps({"kind": "checkpoint", **payload, "signature": signature}) + "\n"


def record_export(workspace_id, from_seq: int, to_seq: int | None, *, actor, now: _dt.datetime):
    head = AuditChainHead.objects.filter(workspace_id=workspace_id).first()
    head_seq = head.seq if head else 0
    upper = min(to_seq, head_seq) if to_seq is not None else head_seq
    with transaction.atomic():
        audit.append_many(
            workspace_id,
            [
                Event(
                    "audit.exported",
                    now,
                    actor_type=actor[0],
                    actor_id=actor[1],
                    payload={"from_seq": from_seq, "to_seq": upper, "checkpoint_seq": upper},
                )
            ],
        )
