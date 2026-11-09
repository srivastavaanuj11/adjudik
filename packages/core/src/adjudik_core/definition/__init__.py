"""Decision definition loading, validation, hashing and sampling (SPEC §5)."""

from __future__ import annotations

from .hashing import definition_hash, rules_hash
from .input_validate import InputIssue, InputValidationError, validate_input
from .loader import CompiledDefinition, DefinitionError, load_definition
from .models import Definition
from .sample import sample_input
from .validate import Issue, validate_definition

__all__ = [
    "CompiledDefinition",
    "Definition",
    "DefinitionError",
    "InputIssue",
    "InputValidationError",
    "Issue",
    "definition_hash",
    "load_definition",
    "rules_hash",
    "sample_input",
    "validate_definition",
    "validate_input",
]
