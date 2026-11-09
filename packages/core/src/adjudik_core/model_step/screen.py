"""Prompt-injection screen (SPEC §7.4).

A cheap first filter — not a guarantee (SPEC §15.4) — that scans every untrusted
string in the minimized input with fixed, case-insensitive patterns containing no
nested quantifiers. Any hit routes the case to a gate review with reason
``suspected_injection`` before the model is ever called. Only ``{path, rule}`` is
recorded, never the matched text.
"""

from __future__ import annotations

import re
from typing import Any

from .minimize import iter_untrusted_strings

__all__ = ["PATTERNS", "screen_flags"]

# SPEC §7.4 — patterns verbatim, compiled case-insensitive.
PATTERNS: dict[str, re.Pattern[str]] = {
    "ignore_instructions": re.compile(
        r"\b(ignore|disregard|forget|override)\b[^\n]{0,40}\b(instruction|prompt|rule|polic|system)",
        re.IGNORECASE,
    ),
    "role_marker": re.compile(
        r"(^|\n)\s*(system|assistant|developer)\s*:|<\||\[/?INST\]",
        re.IGNORECASE,
    ),
    "output_spoof": re.compile(r"\"?\b(outcome|confidence)\b\"?\s*[:=]", re.IGNORECASE),
    "tag_spoof": re.compile(r"</?\s*(case_data|policy)\b", re.IGNORECASE),
    "persona": re.compile(r"\byou are (now )?(an? )?(ai|assistant|model|system)\b", re.IGNORECASE),
}


def screen_flags(input_schema: dict[str, Any], minimized: Any) -> list[dict[str, str]]:
    """Return ``[{path, rule}]`` for every pattern hit on an untrusted string."""
    flags: list[dict[str, str]] = []
    for path, value in iter_untrusted_strings(input_schema, minimized):
        for rule, pattern in PATTERNS.items():
            if pattern.search(value):
                flags.append({"path": path, "rule": rule})
    return flags
