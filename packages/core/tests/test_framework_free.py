"""SPEC §1 rule 9 / M0: ``adjudik_core`` must never import Django, DRF or Celery.

This complements the ``import-linter`` contract. Because pytest-django loads
Django (and this project's Celery app) into the test interpreter before any test
runs, checking the shared ``sys.modules`` would always find them. So the runtime
check imports every ``adjudik_core`` submodule in a *clean* subprocess and asserts
none of the forbidden frameworks ended up loaded there.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import adjudik_core

FORBIDDEN = ("django", "rest_framework", "celery")

_CHECK = textwrap.dedent(
    """
    import importlib, pkgutil, sys
    import adjudik_core

    forbidden = ("django", "rest_framework", "celery")
    names = [adjudik_core.__name__]
    names += [i.name for i in pkgutil.walk_packages(adjudik_core.__path__, "adjudik_core.")]
    for name in names:
        importlib.import_module(name)

    offenders = sorted(
        m for m in sys.modules
        for bad in forbidden
        if m == bad or m.startswith(bad + ".")
    )
    if offenders:
        print("OFFENDERS:" + ",".join(offenders))
        sys.exit(1)
    sys.exit(0)
    """
)


def test_public_api_imports() -> None:
    assert adjudik_core.__version__
    for symbol in ("load_definition", "validate_definition", "Engine"):
        assert hasattr(adjudik_core, symbol)


def test_core_imports_no_frameworks() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _CHECK],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"adjudik_core must not import {FORBIDDEN}; subprocess said: "
        f"{result.stdout.strip()} {result.stderr.strip()}"
    )
