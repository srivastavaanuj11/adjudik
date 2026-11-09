"""The ``adjudik`` command-line interface (SPEC §16.7).

M1 implemented ``validate`` and rules-only ``test``. M2 adds ``render-prompt`` and
``test --with-model [--policy] [--provider]``. ``push`` arrives in M3; ``audit
verify`` in M3; ``bench`` in M5.
"""

from __future__ import annotations

import json
import random
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
    policy: Path | None = typer.Option(None, "--policy", help="automation policy YAML"),
    with_model: bool = typer.Option(False, "--with-model", help="call the model step"),
    provider: str | None = typer.Option(None, "--provider", help="provider override"),
) -> None:
    """Run test cases against a definition; exit 1 on failure (SPEC §16.7)."""
    compiled = _load_or_exit(path)

    engine = None
    automation_policy = None
    if with_model:
        engine, automation_policy = _build_engine(policy)
    elif policy is not None:
        typer.secho("--policy has no effect without --with-model", fg=typer.colors.YELLOW)

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

        outcome: dict[str, Any] | None
        if with_model:
            assert engine is not None
            outcome = _run_with_model(engine, compiled, case["input"], automation_policy, provider)
        else:
            outcome = _run_rules_only(compiled, case["input"], matched_rows)
            if outcome is None:
                skipped += 1
                typer.echo(f"SKIP  {name} (reaches the model; rerun with --with-model)")
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
    if not with_model:
        never = sorted(all_row_ids - matched_rows)
        if never:
            typer.secho(f"rows never matched: {', '.join(never)}", fg=typer.colors.YELLOW)
    raise typer.Exit(1 if failed else 0)


@app.command(name="render-prompt")
def render_prompt(
    path: Path,
    input_path: Path = typer.Option(..., "--input", help="JSON file with one case input"),
) -> None:
    """Print the system prompt and user message for one case (SPEC §16.7)."""
    from ..model_step import build_prompt, minimize_input

    compiled = _load_or_exit(path)
    if compiled.definition.model_step is None:
        typer.secho("this definition has no model_step", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    data = json.loads(input_path.read_text())
    try:
        compiled.validate_input(data)
    except InputValidationError as exc:
        codes = ", ".join(i.code for i in exc.issues)
        typer.secho(f"input is invalid: {codes}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    minimized = minimize_input(
        compiled.input_schema, compiled.definition.model_step.include_fields, data
    )
    system, user = build_prompt(compiled.definition, minimized)
    typer.echo("=== system ===")
    typer.echo(system)
    typer.echo("\n=== user ===")
    typer.echo(user)


def _load_or_exit(path: Path) -> CompiledDefinition:
    try:
        return load_definition(path)
    except DefinitionError as exc:
        for i in exc.issues:
            if i.is_error:
                typer.secho(
                    f"definition error {i.path or '/'} [{i.code}] {i.message}",
                    fg=typer.colors.RED,
                    err=True,
                )
        raise typer.Exit(1) from exc


def _build_engine(policy: Path | None) -> tuple[Any, Any]:
    from adjudik_core import AutomationPolicy, Engine
    from adjudik_core.providers import build_providers

    engine = Engine(providers=build_providers(), rng=random.Random(0))
    automation_policy = AutomationPolicy.load(policy) if policy is not None else None
    return engine, automation_policy


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
        return {"status": "needs_review", "route_reason": "no_rule_matched"}
    return None  # handed to the model


def _run_with_model(
    engine: Any,
    compiled: CompiledDefinition,
    data: Any,
    policy: Any,
    provider: str | None,
) -> dict[str, Any]:
    try:
        result = engine.evaluate(compiled, data, policy=policy, provider_override=provider)
    except InputValidationError as exc:
        return {"status": "input_invalid", "issues": [i.code for i in exc.issues]}
    out: dict[str, Any] = {"status": result.status}
    if result.status == "decided":
        out["outcome"] = result.outcome
        out["decided_by"] = result.decided_by
    else:
        out["route_reason"] = result.route_reason
    return out


def _matches(expect: dict[str, Any], outcome: dict[str, Any]) -> bool:
    return all(outcome.get(k) == v for k, v in expect.items())
