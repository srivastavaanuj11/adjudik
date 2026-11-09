"""The ``adjudik`` command-line interface (SPEC §16.7).

M1 implements ``validate`` and rules-only ``test``. ``render-prompt`` and
``test --with-model`` arrive in M2; ``push`` in M3; ``audit verify`` in M3;
``bench`` in M5.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typer

from ..definition import CompiledDefinition, DefinitionError, load_definition, validate_definition
from ..definition.input_validate import InputValidationError
from ..definition.yaml_loader import YAMLLoadError, load_yaml

app = typer.Typer(
    name="adjudik",
    help="Adjudik decision engine CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def version() -> None:
    """Print the adjudik_core version."""
    from adjudik_core import __version__

    typer.echo(__version__)


@app.command()
def validate(paths: list[Path]) -> None:
    """Validate decision definitions; exit 1 on errors (SPEC §16.7)."""
    any_errors = False
    for path in paths:
        try:
            doc = load_yaml(path.read_text())
        except (OSError, YAMLLoadError) as exc:
            typer.secho(f"{path}: cannot read: {exc}", fg=typer.colors.RED, err=True)
            any_errors = True
            continue
        issues = validate_definition(doc)
        errors = [i for i in issues if i.is_error]
        warnings = [i for i in issues if not i.is_error]
        if not issues:
            typer.secho(f"{path}: OK", fg=typer.colors.GREEN)
        for i in errors:
            typer.secho(
                f"{path}: ERROR {i.path or '/'} [{i.code}] {i.message}",
                fg=typer.colors.RED,
                err=True,
            )
        for i in warnings:
            typer.secho(
                f"{path}: warning {i.path or '/'} [{i.code}] {i.message}", fg=typer.colors.YELLOW
            )
        any_errors = any_errors or bool(errors)
    raise typer.Exit(1 if any_errors else 0)


@app.command()
def test(
    path: Path,
    cases: Path = typer.Option(..., "--cases", help="JSONL case file"),
    policy: Path | None = typer.Option(None, "--policy", help="automation policy (M2+)"),
    with_model: bool = typer.Option(False, "--with-model", help="call the model (M2+)"),
    provider: str | None = typer.Option(None, "--provider", help="provider override (M2+)"),
) -> None:
    """Run test cases against a definition; exit 1 on failure (SPEC §16.7)."""
    if with_model or policy is not None or provider is not None:
        typer.secho(
            "--with-model/--policy/--provider require the model step (M2); only "
            "rules-only `adjudik test` is available in M1.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(2)

    try:
        compiled = load_definition(path)
    except DefinitionError as exc:
        for i in exc.issues:
            if i.is_error:
                typer.secho(
                    f"definition error {i.path or '/'} [{i.code}] {i.message}",
                    fg=typer.colors.RED,
                    err=True,
                )
        raise typer.Exit(1) from exc

    passed = failed = skipped = 0
    matched_rows: set[str] = set()
    all_row_ids = {r.id for r in (compiled.rule_table.rows if compiled.rule_table else ())}

    for lineno, line in enumerate(cases.read_text().splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        case = json.loads(line)
        name = case.get("name", f"line {lineno}")
        expect = case.get("expect", {})
        outcome = _run_rules_only(compiled, case["input"], matched_rows)
        if outcome is None:
            skipped += 1
            typer.echo(f"SKIP  {name} (reaches the model; rerun with --with-model in M2)")
            continue
        if _matches(expect, outcome):
            passed += 1
            typer.secho(f"PASS  {name}", fg=typer.colors.GREEN)
        else:
            failed += 1
            typer.secho(
                f"FAIL  {name}: expected {expect}, got {outcome}", fg=typer.colors.RED, err=True
            )

    typer.echo(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    never = sorted(all_row_ids - matched_rows)
    if never:
        typer.secho(f"rows never matched: {', '.join(never)}", fg=typer.colors.YELLOW)
    raise typer.Exit(1 if failed else 0)


def _run_rules_only(
    compiled: CompiledDefinition, data: Any, matched_rows: set[str]
) -> dict[str, Any] | None:
    """Return the rules-only outcome dict, or ``None`` if the case reaches the model."""
    try:
        compiled.validate_input(data)
    except InputValidationError as exc:
        return {"status": "input_invalid", "issues": [i.code for i in exc.issues]}

    result, _trace = compiled.evaluate_rules(data)
    matched_rows.update(result.matched_row_ids)

    if result.type == "decide":
        return {"status": "decided", "outcome": result.outcome, "decided_by": "rule"}
    if result.type == "review":
        return {"status": "needs_review", "route_reason": "rule_forced_review"}
    if result.type == "conflict":
        return {"status": "needs_review", "route_reason": "rule_conflict"}
    # result.type is "model" or "no_match".
    on_no_match = compiled.rule_table.on_no_match if compiled.rule_table else "model"
    if result.type == "no_match" and on_no_match == "review":
        return {"status": "needs_review", "route_reason": "no_match_review"}
    return None  # handed to the model


def _matches(expect: dict[str, Any], outcome: dict[str, Any]) -> bool:
    return all(outcome.get(k) == v for k, v in expect.items())
