"""Adjudik core: the framework-free decision engine.

This package must never import Django, DRF or Celery (SPEC §1 rule 9); the rule is
enforced by ``import-linter`` and by ``tests/test_framework_free.py``.

M1 implements definitions and rules (SPEC §5, §6); M2 adds the model step and the
engine (SPEC §4.5 ``Engine``, ``AutomationPolicy``, ``EvaluationContext``, §7, §8).
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
from .engine import (
    AuditSample,
    AutomationPolicy,
    Clock,
    Decided,
    Engine,
    EngineEvent,
    EvaluationContext,
    EvaluationResult,
    Gate,
    PolicyError,
    ReviewSpec,
    Routing,
    SystemClock,
)

__version__ = "0.0.0"

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
    "EvaluationResult",
    "EngineEvent",
    "ReviewSpec",
    "AuditSample",
    "Decided",
    "Gate",
    "Routing",
    "Clock",
    "SystemClock",
    "PolicyError",
]
