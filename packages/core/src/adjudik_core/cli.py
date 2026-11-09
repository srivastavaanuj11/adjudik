"""The ``adjudik`` command-line interface (SPEC §16.7).

M0 registers the command surface so ``adjudik --help`` works; each command's body
is implemented in its milestone (validate/test/render-prompt in M1–M2, push/audit
in M3, bench in M5).
"""

from __future__ import annotations

import typer

app = typer.Typer(
    name="adjudik",
    help="Adjudik decision engine CLI.",
    no_args_is_help=True,
    add_completion=False,
)


@app.command()
def validate(paths: list[str]) -> None:
    """Validate decision definitions; exit 1 on errors (M1)."""
    raise typer.Exit(_not_yet("validate", "M1"))


@app.command()
def version() -> None:
    """Print the adjudik_core version."""
    from adjudik_core import __version__

    typer.echo(__version__)


def _not_yet(name: str, milestone: str) -> int:
    typer.echo(f"`adjudik {name}` is not implemented until {milestone}.", err=True)
    return 1


if __name__ == "__main__":
    app()
