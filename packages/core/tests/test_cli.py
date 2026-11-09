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


def test_with_model_flag_is_rejected_in_m1() -> None:
    ex = "examples/refund_approval"
    result = runner.invoke(
        app, ["test", f"{ex}/decision.yaml", "--cases", f"{ex}/cases.jsonl", "--with-model"]
    )
    assert result.exit_code == 2


def test_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0 and result.output.strip()
