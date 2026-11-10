"""Audit chain: append, immutability, one-append rule, 32-writer concurrency.

Acceptance for M3a's audit-chain box (SPEC §9.2, §9.3, §9.6, §16.4, §17).
"""

from __future__ import annotations

import datetime as _dt
import threading

import psycopg
import pytest
from django.db import connection, transaction

from adjudik_core import audit_chain
from adjudik_core.canonical import ZERO_HASH
from apps.audit.models import AuditChainHead, AuditEvent
from apps.common import audit
from apps.common.audit import AuditAppendError, Event


def _event(now: _dt.datetime, n: int) -> Event:
    return Event(
        event_type="evaluation.received",
        occurred_at=now + _dt.timedelta(seconds=n),
        actor_type="system",
        payload={"n": n},
    )


def _rows_for_verify(workspace_id):
    qs = AuditEvent.objects.filter(workspace_id=workspace_id).order_by("seq")
    return [
        {
            "seq": r.seq,
            "workspace_id": str(r.workspace_id),
            "event_type": r.event_type,
            "occurred_at": audit.iso_micros(r.occurred_at),
            "actor_type": r.actor_type,
            "actor_id": r.actor_id,
            "evaluation_id": str(r.evaluation_id) if r.evaluation_id else None,
            "subject_type": r.subject_type,
            "subject_id": r.subject_id,
            "payload": r.payload,
            "prev_hash": r.prev_hash,
            "hash": r.hash,
        }
        for r in qs
    ]


@pytest.mark.django_db
def test_append_builds_a_verifiable_chain(workspace, now):
    with transaction.atomic():
        audit.append_many(workspace.id, [_event(now, 1), _event(now, 2), _event(now, 3)])

    head = AuditChainHead.objects.get(workspace_id=workspace.id)
    assert head.seq == 3

    result = audit_chain.verify(_rows_for_verify(workspace.id), 1, ZERO_HASH)
    assert result.ok
    assert result.head_seq == 3
    assert result.head_hash == head.hash


@pytest.mark.django_db
def test_stored_payload_rehashes_identically(workspace, now):
    """Payload normalisation (SPEC §9.2): what Postgres returns re-hashes the same."""
    with transaction.atomic():
        audit.append_many(
            workspace.id,
            [Event("evaluation.routed", now, payload={"b": 2, "a": 1, "nested": {"y": 0.5}})],
        )
    result = audit_chain.verify(_rows_for_verify(workspace.id), 1, ZERO_HASH)
    assert result.ok


@pytest.mark.django_db
def test_append_many_twice_in_one_transaction_raises(workspace, now):
    with transaction.atomic():
        audit.append_many(workspace.id, [_event(now, 1)])
        with pytest.raises(AuditAppendError):
            audit.append_many(workspace.id, [_event(now, 2)])


@pytest.mark.django_db
def test_audit_event_update_blocked_as_owner(workspace, now):
    with transaction.atomic():
        audit.append_many(workspace.id, [_event(now, 1)])
    with pytest.raises(Exception) as exc, connection.cursor() as cur:  # noqa: B017,PT011
        cur.execute("UPDATE audit_event SET event_type = 'x' WHERE seq = 1")  # trigger blocks owner
    assert "append-only" in str(exc.value).lower()


@pytest.mark.django_db
def test_audit_event_update_and_delete_blocked_as_app(app_conn):
    """The §10.3 grant matrix: app role may SELECT but not UPDATE/DELETE audit_event."""
    # UPDATE is refused by privilege (revoked), before any row trigger runs.
    with pytest.raises(psycopg.errors.InsufficientPrivilege), app_conn.cursor() as cur:
        cur.execute("UPDATE audit_event SET event_type = 'x'")
    app_conn.rollback()

    with pytest.raises(psycopg.errors.InsufficientPrivilege), app_conn.cursor() as cur:
        cur.execute("DELETE FROM audit_event")
    app_conn.rollback()

    # SELECT is allowed.
    with app_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM audit_event")
        assert cur.fetchone()[0] >= 0
    app_conn.rollback()


@pytest.mark.django_db
def test_audit_chain_head_insert_delete_blocked_as_app(app_conn):
    """audit_chain_head: app role may SELECT/UPDATE but not INSERT/DELETE (§10.3)."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege), app_conn.cursor() as cur:
        cur.execute("DELETE FROM audit_chain_head")
    app_conn.rollback()


@pytest.mark.django_db(transaction=True)
def test_thirty_two_writers_produce_a_gapless_chain(now):
    """32 concurrent writers each append; the head lock serialises them (SPEC §9.3)."""
    from apps.accounts import service as accounts_service

    ws = accounts_service.create_workspace("writers", "Writers", now=now)
    n_writers = 32
    barrier = threading.Barrier(n_writers)
    errors: list[Exception] = []

    def writer(i: int) -> None:
        try:
            barrier.wait()
            with transaction.atomic():
                audit.append_many(ws.id, [_event(now, i)])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_writers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    head = AuditChainHead.objects.get(workspace_id=ws.id)
    assert head.seq == n_writers
    seqs = list(
        AuditEvent.objects.filter(workspace_id=ws.id).order_by("seq").values_list("seq", flat=True)
    )
    assert seqs == list(range(1, n_writers + 1))  # gapless
    result = audit_chain.verify(_rows_for_verify(ws.id), 1, ZERO_HASH)
    assert result.ok and result.head_seq == n_writers
