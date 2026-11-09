"""Extra coverage: M2 stubs, sample_input edges, and CLI error branches."""

from __future__ import annotations

from typer.testing import CliRunner

from adjudik_core import Engine, sample_input
from adjudik_core.cli import app
from adjudik_core.providers import build_providers

runner = CliRunner()


def test_engine_and_providers_are_available() -> None:
    providers = build_providers()
    assert "demo" in providers
    engine = Engine(providers=providers)
    assert engine.providers is providers


def test_sample_input_respects_const_enum_min_and_arrays() -> None:
    schema = {
        "type": "object",
        "properties": {
            "c": {"const": "fixed"},
            "e": {"enum": ["first", "second"]},
            "s": {"type": "string", "minLength": 3},
            "num": {"type": "number", "minimum": 5},
            "arr": {"type": "array", "minItems": 2, "items": {"type": "string"}},
            "arr0": {"type": "array"},
            "b": {"type": "boolean"},
            "nul": {"type": ["integer", "null"]},
            "untyped": {},
        },
    }
    si = sample_input(schema)
    assert si["c"] == "fixed"
    assert si["e"] == "first"
    assert si["s"] == "xxx"
    assert si["num"] == 5
    assert si["arr"] == ["x", "x"]
    assert si["arr0"] == []
    assert si["b"] is False
    assert si["nul"] == 0
    assert si["untyped"] is None


def test_sample_input_non_object_returns_empty() -> None:
    assert sample_input({"type": "string"}) == {}


def test_cli_validate_unreadable_file() -> None:
    result = runner.invoke(app, ["validate", "does/not/exist.yaml"])
    assert result.exit_code == 1
    assert "cannot read" in result.output


def test_cli_test_rejects_bad_definition(tmp_path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "schema_version: '1'\nkey: BAD\nname: x\ninput_schema: {type: object}\noutcomes: []\n"
    )
    cases = tmp_path / "c.jsonl"
    cases.write_text("")
    result = runner.invoke(app, ["test", str(bad), "--cases", str(cases)])
    assert result.exit_code == 1
    assert "definition error" in result.output


def test_cli_test_reports_rows_never_matched(tmp_path) -> None:
    cases = tmp_path / "c.jsonl"
    # Only exercises the cancelled_order row; the others never match.
    cases.write_text(
        '{"name": "cancel", "input": {"order": {"id": "A", "amount": 10.0, "currency": "USD", '
        '"status": "cancelled", "days_since_delivery": null}, "customer": {"id": "C", "tier": '
        '"standard", "refunds_last_90d": 0}, "request": {"reason_text": "x"}}, '
        '"expect": {"status": "decided", "outcome": "approve", "decided_by": "rule"}}\n'
    )
    result = runner.invoke(
        app, ["test", "examples/refund_approval/decision.yaml", "--cases", str(cases)]
    )
    assert result.exit_code == 0
    assert "rows never matched" in result.output
    assert "outside_window" in result.output
