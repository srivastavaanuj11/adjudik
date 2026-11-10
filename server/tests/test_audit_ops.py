"""Audit checkpoints, verification and export (SPEC §9.5, §9.6, §17 M3a).

Checkpoints are signed; the three verification modes run; and the NDJSON export
is accepted by ``adjudik audit verify`` while a tampered or truncated export is
rejected.
"""

from __future__ import annotations

import datetime as _dt

import pytest
from django.db import transaction
from typer.testing import CliRunner

from adjudik_core import audit_chain
from adjudik_core.cli import app as cli_app
from apps.accounts.models import Role
from apps.audit import service
from apps.audit.models import VerificationMode
from apps.common import audit
from apps.common.audit import Event

runner = CliRunner()


def _append(workspace, now, count):
    with transaction.atomic():
        audit.append_many(
            workspace.id,
            [
                Event("evaluation.received", now + _dt.timedelta(seconds=i), payload={"n": i})
                for i in range(count)
            ],
        )


@pytest.mark.django_db
def test_checkpoint_is_signed_and_verifies(workspace, now):
    _append(workspace, now, 3)
    cp = service.write_checkpoint(workspace.id, now)
    assert cp is not None and cp.seq == 3
    payload = {
        "workspace_id": str(workspace.id),
        "seq": cp.seq,
        "hash": cp.hash,
        "created_at": audit.iso_micros(cp.created_at),
        "key_id": cp.key_id,
        "signature": cp.signature,
    }
    assert audit_chain.verify_checkpoint(payload, service.public_key_b64())


@pytest.mark.django_db
def test_checkpoint_skipped_without_new_events(workspace, now):
    _append(workspace, now, 2)
    assert service.write_checkpoint(workspace.id, now) is not None
    assert service.write_checkpoint(workspace.id, now) is None  # nothing new


@pytest.mark.django_db
def test_verification_modes(workspace, now):
    _append(workspace, now, 5)
    service.write_checkpoint(workspace.id, now)

    full = service.run_verification(workspace.id, VerificationMode.FULL, requested_by=None, now=now)
    assert full.ok and full.head_seq == 5 and full.from_seq == 1

    incr = service.run_verification(
        workspace.id, VerificationMode.INCREMENTAL, requested_by=None, now=now
    )
    assert incr.ok and incr.from_seq == 6  # after the checkpoint at seq 5

    recon = service.run_verification(
        workspace.id, VerificationMode.RECONCILE, requested_by=None, now=now
    )
    assert recon.ok


@pytest.mark.django_db
def test_reconcile_detects_definition_hash_mismatch(workspace, now):
    from apps.decisions.models import Decision, DecisionVersion, VersionStatus

    _append(workspace, now, 2)
    decision = Decision.objects.create(workspace=workspace, key="d", name="D", created_at=now)
    DecisionVersion.objects.create(
        workspace=workspace,
        decision=decision,
        version=1,
        status=VersionStatus.ACTIVE,
        definition={"schema_version": "1", "key": "d", "name": "D", "input_schema": {}},
        source_format="json",
        definition_hash="deadbeef",  # does not match the stored definition
        rules_hash="",
        created_at=now,
        updated_at=now,
    )
    recon = service.run_verification(
        workspace.id, VerificationMode.RECONCILE, requested_by=None, now=now
    )
    assert recon.ok is False
    assert any("definition" in m.get("reason", "") for m in recon.mismatches)


@pytest.mark.django_db
def test_export_endpoint_and_cli_verify(api_client, workspace, make_member, now, tmp_path):
    _append(workspace, now, 4)
    service.write_checkpoint(workspace.id, now)
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)

    r = api_client.get("/api/v1/audit/export")
    assert r.status_code == 200
    body = b"".join(r.streaming_content).decode("utf-8")
    assert '"kind": "event"' in body and '"kind": "checkpoint"' in body

    export = tmp_path / "audit.ndjson"
    export.write_text(body)
    pk = service.public_key_b64()

    good = runner.invoke(cli_app, ["audit", "verify", str(export), "--public-key", pk])
    assert good.exit_code == 0, good.output

    # Truncation: drop the trailing export-time checkpoint -> rejected.
    lines = body.splitlines()
    truncated = tmp_path / "trunc.ndjson"
    truncated.write_text("\n".join(lines[:-1]) + "\n")
    bad_trunc = runner.invoke(cli_app, ["audit", "verify", str(truncated), "--public-key", pk])
    assert bad_trunc.exit_code == 1

    # Tampering: flip a digit in an event's stored hash -> chain mismatch.
    tampered_text = body.replace('"n": 0', '"n": 99', 1)
    tampered = tmp_path / "tamper.ndjson"
    tampered.write_text(tampered_text)
    bad_tamper = runner.invoke(cli_app, ["audit", "verify", str(tampered), "--public-key", pk])
    assert bad_tamper.exit_code == 1


@pytest.mark.django_db
def test_public_key_endpoint(api_client, workspace, make_member):
    admin = make_member(workspace, Role.ADMIN)
    api_client.force_login(admin)
    r = api_client.get("/api/v1/audit/public-key")
    assert r.status_code == 200
    assert r.json()["public_key"] and len(r.json()["key_id"]) == 16
