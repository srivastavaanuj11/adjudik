"""Adjudik core: the framework-free decision engine.

This package must never import Django, DRF or Celery (SPEC §1 rule 9); the rule is
enforced by ``import-linter`` and by ``tests/test_framework_free.py``.

M1 implements definitions and rules (SPEC §5, §6). The model step and engine
(SPEC §4.5 ``Engine``, ``AutomationPolicy``, ``EvaluationContext``) land in M2 and
are placeholders until then.
"""

from __future__ import annotations

from .definition import (
    CompiledDefinition,
    Definition,
    DefinitionError,
    InputValidationError,
    Issue,
    load_definition,
    sample_input,
    validate_definition,
)

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


# SPEC §4.5 public API — the model step and engine land in M2.
Engine = _NotYet("Engine", "M2")
AutomationPolicy = _NotYet("AutomationPolicy", "M2")
EvaluationContext = _NotYet("EvaluationContext", "M2")

__all__ = [
    "__version__",
    "CompiledDefinition",
    "Definition",
    "DefinitionError",
    "InputValidationError",
    "Issue",
    "load_definition",
    "validate_definition",
    "sample_input",
    "Engine",
    "AutomationPolicy",
    "EvaluationContext",
]
