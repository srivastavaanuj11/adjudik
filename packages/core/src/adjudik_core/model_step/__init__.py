"""The model step: minimisation, injection screen, prompt, run and fingerprint (SPEC §7)."""

from __future__ import annotations

from .fingerprint import model_step_fingerprint
from .minimize import iter_untrusted_strings, minimize_input
from .parse import ParsedAttempt, parse_attempt
from .prompt import (
    PROMPT_VERSION,
    build_prompt,
    build_system_prompt,
    build_user_message,
    local_output_schema,
    provider_safe_schema,
)
from .result import CallRecord, ModelStepResult
from .runner import MAX_REQUESTS_PER_STEP, run_model_step
from .screen import PATTERNS, screen_flags

__all__ = [
    "minimize_input",
    "iter_untrusted_strings",
    "screen_flags",
    "PATTERNS",
    "build_prompt",
    "build_system_prompt",
    "build_user_message",
    "local_output_schema",
    "provider_safe_schema",
    "PROMPT_VERSION",
    "parse_attempt",
    "ParsedAttempt",
    "run_model_step",
    "MAX_REQUESTS_PER_STEP",
    "CallRecord",
    "ModelStepResult",
    "model_step_fingerprint",
]
