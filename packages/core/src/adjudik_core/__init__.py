"""Adjudik core: the framework-free decision engine.

This package must never import Django, DRF or Celery (SPEC §1 rule 9); the rule is
enforced by ``import-linter`` and by ``tests/test_framework_free.py``.

M0 provides the public-API surface from SPEC §4.5 as stubs. Real implementations
arrive in M1 (definitions, rules) and M2 (model step, engine). Stubs raise
``NotImplementedError`` so that imports work and wiring can be tested, without
pretending the logic exists.
"""

from __future__ import annotations

__version__ = "0.0.0"


class _NotYet:
    """Placeholder for a public symbol whose implementation lands in a later milestone."""

    def __init__(self, name: str, milestone: str) -> None:
        self._name = name
        self._milestone = milestone

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise NotImplementedError(
            f"adjudik_core.{self._name} is not implemented until milestone {self._milestone}"
        )


# SPEC §4.5 public API — implemented in M1/M2.
load_definition = _NotYet("load_definition", "M1")
validate_definition = _NotYet("validate_definition", "M1")
sample_input = _NotYet("sample_input", "M1")
Engine = _NotYet("Engine", "M2")
AutomationPolicy = _NotYet("AutomationPolicy", "M2")
EvaluationContext = _NotYet("EvaluationContext", "M2")

__all__ = [
    "__version__",
    "load_definition",
    "validate_definition",
    "sample_input",
    "Engine",
    "AutomationPolicy",
    "EvaluationContext",
]
