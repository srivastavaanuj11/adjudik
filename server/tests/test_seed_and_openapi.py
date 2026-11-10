"""Demo seed and OpenAPI generation (SPEC §16.5, §17 M3b)."""

from __future__ import annotations

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.decisions.models import AutomationPolicy, Decision, PolicyStatus
from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask


@pytest.mark.django_db(transaction=True)
@override_settings(DEBUG=True)
def test_seed_demo_builds_workspace_and_labels():
    # Scaled down so the test is quick; the mechanics are identical at full size.
    call_command("seed_demo", refunds=25, support=60, seed=1)

    from apps.accounts.models import Workspace

    ws = Workspace.objects.get(slug="demo")
    refund = Decision.objects.get(workspace=ws, key="refund_approval")
    support = Decision.objects.get(workspace=ws, key="support_routing")

    # Refund has its example policy; support routing runs in shadow mode (none).
    assert AutomationPolicy.objects.filter(
        decision_version__decision=refund, status=PolicyStatus.ACTIVE
    ).exists()
    assert not AutomationPolicy.objects.filter(
        decision_version__decision=support, status=PolicyStatus.ACTIVE
    ).exists()

    # At least 20 gate tasks stay open so the queue has work (SPEC §16.5).
    open_gate = ReviewTask.objects.filter(
        workspace=ws, kind=ReviewKind.GATE, status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED]
    ).count()
    assert open_gate >= 20

    # Until M5, the readiness check is on label counts per outcome: completed
    # review tasks carry a final_outcome, and more than one outcome is labelled.
    completed = ReviewTask.objects.filter(
        workspace=ws, decision=support, status=ReviewStatus.COMPLETED
    ).exclude(final_outcome__isnull=True)
    outcomes = {t.final_outcome for t in completed}
    assert completed.count() > 0
    assert len(outcomes) >= 2


@pytest.mark.django_db
@override_settings(DEBUG=False)
def test_seed_demo_refuses_without_debug():
    with pytest.raises(CommandError):
        call_command("seed_demo", refunds=1, support=1)


@pytest.mark.django_db
def test_openapi_schema_generates():
    from drf_spectacular.generators import SchemaGenerator

    schema = SchemaGenerator().get_schema(request=None, public=True)
    assert schema["openapi"].startswith("3.")
    paths = schema["paths"]
    # A representative sample of M3b endpoints is present.
    assert any("/reviews" in p for p in paths)
    assert any("/webhooks" in p for p in paths)
    assert any("metrics" in p for p in paths)
    assert any("/evaluations/{id}/cancel" in p or "cancel" in p for p in paths)
