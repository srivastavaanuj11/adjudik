"""Decision metrics against a deterministic fixture (SPEC §11.7, §17 M3b).

Every figure below is hand-computed from the rows the fixture creates; the
Clopper-Pearson bounds are checked against an independent ``scipy`` call.
"""

from __future__ import annotations

import datetime as _dt
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from scipy.stats import beta

from adjudik_core import load_definition
from apps.decisions.models import Decision, DecisionVersion, VersionStatus
from apps.evaluations.models import (
    DecidedBy,
    Evaluation,
    EvaluationStatus,
    ModelCall,
    ModelCallStatus,
)
from apps.metrics import service
from apps.reviews.models import ReviewAction, ReviewKind, ReviewStatus, ReviewTask

EXAMPLES = Path(__file__).resolve().parents[2] / "examples"
DAY = _dt.datetime(2026, 11, 9, tzinfo=_dt.UTC)
START = DAY
END = DAY + _dt.timedelta(days=1)
T = DAY + _dt.timedelta(hours=10)  # creation instant, inside the window


def _active_version(workspace, now):
    d = yaml.safe_load((EXAMPLES / "support_routing" / "decision.yaml").read_text())
    d["model_step"]["provider"] = "demo"
    d["model_step"]["model"] = "demo-1"
    compiled = load_definition(d)
    decision = Decision.objects.create(
        workspace=workspace, key=compiled.key, name=compiled.definition.name, created_at=now
    )
    version = DecisionVersion.objects.create(
        workspace=workspace,
        decision=decision,
        version=1,
        status=VersionStatus.ACTIVE,
        definition=compiled.definition.model_dump(mode="json"),
        source_text="",
        source_format="json",
        definition_hash=compiled.definition_hash,
        rules_hash=compiled.rules_hash or "",
        model_step_fingerprint="fp",
        created_at=now,
        updated_at=now,
        published_at=now,
    )
    return decision, version


def _eval(ws, dec, ver, *, status, decided_by=None, handed=False, latency_ms=None, cancelled=False):
    return Evaluation.objects.create(
        workspace=ws,
        decision=dec,
        decision_version=ver,
        salt=b"0" * 32,
        status=status,
        state_version=1,
        handed_to_model=handed,
        outcome="billing" if decided_by else None,
        decided_by=decided_by,
        created_at=T,
        decided_at=T + _dt.timedelta(milliseconds=latency_ms) if latency_ms is not None else None,
        cancelled_at=T if cancelled else None,
        updated_at=T,
    )


def _gate(ws, dec, evaluation, *, reason, status, action=None, agrees=None, turnaround_min=None):
    return ReviewTask.objects.create(
        workspace=ws,
        decision=dec,
        evaluation=evaluation,
        kind=ReviewKind.GATE,
        priority=0,
        reason=reason,
        suggested_outcome="billing",
        suggested_confidence=0.9,
        blind=False,
        status=status,
        action=action,
        agrees_with_model=agrees,
        completed_at=T + _dt.timedelta(minutes=turnaround_min)
        if turnaround_min is not None
        else None,
        due_at=T + _dt.timedelta(minutes=240),
        created_at=T,
    )


def _audit(ws, dec, evaluation, *, agrees):
    return ReviewTask.objects.create(
        workspace=ws,
        decision=dec,
        evaluation=evaluation,
        kind=ReviewKind.AUDIT,
        priority=1,
        reason="audit_sample",
        suggested_outcome="billing",
        blind=True,
        label_propensity=0.25,
        status=ReviewStatus.COMPLETED,
        action=ReviewAction.DECIDE,
        final_outcome="billing" if agrees else "technical",
        agrees_with_model=agrees,
        completed_at=T,
        due_at=T - _dt.timedelta(minutes=1),
        created_at=T,
    )


@pytest.fixture
def fixture(workspace, now):
    dec, ver = _active_version(workspace, now)
    # Rule-decided: latencies 10/20/30 ms.
    for ms in (10, 20, 30):
        _eval(
            workspace,
            dec,
            ver,
            status=EvaluationStatus.DECIDED,
            decided_by=DecidedBy.RULE,
            latency_ms=ms,
        )
    # Model-decided: latencies 100/200 ms; both handed to model and audited.
    m1 = _eval(
        workspace,
        dec,
        ver,
        status=EvaluationStatus.DECIDED,
        decided_by=DecidedBy.MODEL,
        handed=True,
        latency_ms=100,
    )
    m2 = _eval(
        workspace,
        dec,
        ver,
        status=EvaluationStatus.DECIDED,
        decided_by=DecidedBy.MODEL,
        handed=True,
        latency_ms=200,
    )
    _audit(workspace, dec, m1, agrees=True)
    _audit(workspace, dec, m2, agrees=False)
    # Human-decided via completed visible gate tasks: one accept, one override.
    h1 = _eval(
        workspace,
        dec,
        ver,
        status=EvaluationStatus.DECIDED,
        decided_by=DecidedBy.HUMAN,
        handed=True,
    )
    h2 = _eval(
        workspace,
        dec,
        ver,
        status=EvaluationStatus.DECIDED,
        decided_by=DecidedBy.HUMAN,
        handed=True,
    )
    _gate(
        workspace,
        dec,
        h1,
        reason="low_confidence",
        status=ReviewStatus.COMPLETED,
        action=ReviewAction.ACCEPT,
        agrees=True,
        turnaround_min=30,
    )
    _gate(
        workspace,
        dec,
        h2,
        reason="low_confidence",
        status=ReviewStatus.COMPLETED,
        action=ReviewAction.OVERRIDE,
        agrees=False,
        turnaround_min=90,
    )
    # One still-pending case with an open gate task.
    p1 = _eval(workspace, dec, ver, status=EvaluationStatus.PENDING_REVIEW, handed=True)
    _gate(workspace, dec, p1, reason="automation_disabled", status=ReviewStatus.OPEN)
    # One cancelled case (excluded from volume).
    _eval(workspace, dec, ver, status=EvaluationStatus.CANCELLED, cancelled=True)
    # Model calls for tokens/cost.
    for ev, it, ot in ((m1, 100, 50), (m2, 200, 70)):
        ModelCall.objects.create(
            workspace=workspace,
            evaluation=ev,
            status=ModelCallStatus.COMPLETED,
            purpose="primary",
            provider="demo",
            model_requested="demo-1",
            request_hash="h",
            input_tokens=it,
            output_tokens=ot,
            cost_usd=Decimal("0.01"),
            created_at=T,
        )
    return dec


@pytest.mark.django_db
def test_core_counts(fixture, now):
    m = service.compute(fixture, start=START, end=END, version=None, now=T)
    assert m["volume"] == 8
    assert m["decided_by"] == {"rule": 3, "model": 2, "human": 2}
    assert m["automation_rate"] == round(5 / 8, 6)
    assert m["model_automation_rate"] == round(2 / 5, 6)
    assert m["review_rate"] == round(3 / 8, 6)
    assert m["override_rate"] == 0.5
    assert m["route_reasons"] == {"automation_disabled": 1, "low_confidence": 2}
    assert m["queue"] == {"open": 1, "claimed": 0, "overdue": 0}
    assert m["tokens"] == {"input": 300, "output": 120}
    assert m["cost_usd"] == pytest.approx(0.02)


@pytest.mark.django_db
def test_latency_and_turnaround(fixture):
    m = service.compute(fixture, start=START, end=END, version=None, now=T)
    assert m["latency_ms"]["rule"] == {"p50": 20.0, "p95": 29.0}
    assert m["latency_ms"]["model"] == {"p50": 150.0, "p95": 195.0}
    assert m["review_turnaround_min"] == {"p50": 60.0, "p95": 87.0}
    assert m["daily"][0]["turnaround_p50_min"] == 60.0
    assert m["daily"][0]["volume"] == 8


@pytest.mark.django_db
def test_audit_agreement_matches_scipy(fixture):
    m = service.compute(fixture, start=START, end=END, version=None, now=T)
    agg = m["audit_agreement"]
    # Two audits, propensity 0.25 each -> effective n = 8, effective k = 4.
    assert agg["n"] == 2
    assert agg["effective_n"] == 8.0
    assert agg["point"] == 0.5
    assert agg["lower"] == round(float(beta.ppf(0.025, 4, 5)), 6)
    assert agg["upper"] == round(float(beta.ppf(0.975, 5, 4)), 6)
    assert m["audit_completion_rate"]["rate"] == 1.0
    assert m["audit_completion_rate"]["open_backlog"] == 0


@pytest.mark.django_db
def test_metrics_endpoint_and_cache(api_client, workspace, make_member, fixture, now):
    from rest_framework.test import APIClient

    reader = make_member(workspace, "auditor", username="met-aud")
    client = APIClient()
    client.force_login(reader)
    r = client.get("/api/v1/decisions/support_routing/metrics")
    assert r.status_code == 200
    assert "volume" in r.json() and "daily" in r.json()
