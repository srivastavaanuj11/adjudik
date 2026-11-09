"""CLI: `adjudik validate` and rules-only `adjudik test` (SPEC §16.7, §17 M1)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from adjudik_core.cli import app

runner = CliRunner()

EXAMPLES = ["examples/refund_approval", "examples/support_routing"]


@pytest.mark.parametrize("ex", EXAMPLES)
def test_validate_examples_ok(ex: str) -> None:
    result = runner.invoke(app, ["validate", f"{ex}/decision.yaml"])
    assert result.exit_code == 0, result.output
    assert "OK" in result.output


@pytest.mark.parametrize("ex", EXAMPLES)
def test_test_examples_pass(ex: str) -> None:
    result = runner.invoke(app, ["test", f"{ex}/decision.yaml", "--cases", f"{ex}/cases.jsonl"])
    assert result.exit_code == 0, result.output
    assert "failed" in result.output and " 0 failed" in result.output


def test_validate_reports_errors_and_exits_1(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: '1'\nkey: Bad-Key\nname: x\n")
    result = runner.invoke(app, ["validate", str(bad)])
    assert result.exit_code == 1
    assert "ERROR" in result.output


@pytest.mark.parametrize("ex", EXAMPLES)
def test_with_model_demo_passes_on_examples(ex: str) -> None:
    result = runner.invoke(
        app,
        [
            "test",
            f"{ex}/decision.yaml",
            "--cases",
            f"{ex}/cases.jsonl",
            "--with-model",
            "--provider",
            "demo",
        ],
    )
    assert result.exit_code == 0, result.output
    assert " 0 failed" in result.output and "skipped" in result.output
    assert "0 skipped" in result.output  # with --with-model nothing is skipped


def test_with_model_and_policy_can_auto_decide(tmp_path) -> None:
    policy = tmp_path / "policy.yaml"
    policy.write_text("thresholds: {approve: 0.5, deny: 0.5}\naudit_sample_rate: 0.05\n")
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        '{"name": "damaged approves", "input": {"order": {"id": "A", "amount": 120.0, '
        '"currency": "USD", "status": "delivered", "days_since_delivery": 4}, "customer": '
        '{"id": "C", "tier": "standard", "refunds_last_90d": 0}, "request": {"reason_code": '
        '"damaged", "reason_text": "the item arrived damaged and broken"}}, '
        '"expect": {"status": "decided", "decided_by": "model"}}\n'
    )
    result = runner.invoke(
        app,
        [
            "test",
            "examples/refund_approval/decision.yaml",
            "--cases",
            str(cases),
            "--with-model",
            "--provider",
            "demo",
            "--policy",
            str(policy),
        ],
    )
    assert result.exit_code == 0, result.output
    assert " 0 failed" in result.output


def test_render_prompt_prints_system_and_user(tmp_path) -> None:
    case = tmp_path / "case.json"
    case.write_text(
        '{"order": {"id": "A", "amount": 120.0, "currency": "USD", "status": "delivered", '
        '"days_since_delivery": 4}, "customer": {"id": "C", "tier": "standard", '
        '"refunds_last_90d": 0}, "request": {"reason_code": "damaged", "reason_text": "broke"}}'
    )
    result = runner.invoke(
        app, ["render-prompt", "examples/refund_approval/decision.yaml", "--input", str(case)]
    )
    assert result.exit_code == 0, result.output
    assert "=== system ===" in result.output and "=== user ===" in result.output
    assert "<case_data>" in result.output


def test_render_prompt_rejects_invalid_input(tmp_path) -> None:
    case = tmp_path / "case.json"
    case.write_text('{"order": {}}')
    result = runner.invoke(
        app, ["render-prompt", "examples/refund_approval/decision.yaml", "--input", str(case)]
    )
    assert result.exit_code == 1
    assert "input is invalid" in result.output


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.output.strip()
