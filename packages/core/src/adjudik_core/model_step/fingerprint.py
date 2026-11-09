"""Model-step fingerprint (SPEC §7.11).

A stable hash over the parts of the model step that change model behaviour,
including the provider's configuration (never its keys). Timeouts and retry
counts are deliberately excluded. Because provider configuration is part of it,
the fingerprint is computed against a concrete provider — on the server, at
publish time, against its ``ADJUDIK_PROVIDERS``.
"""

from __future__ import annotations

from ..canonical import sha256_hex
from ..definition.models import Definition
from ..providers.base import ModelProvider
from .prompt import PROMPT_VERSION

__all__ = ["model_step_fingerprint"]


def model_step_fingerprint(defn: Definition, provider: ModelProvider) -> str:
    assert defn.model_step is not None
    step = defn.model_step
    payload = {
        "provider": provider.config_fingerprint,
        "model": step.model,
        "temperature": step.temperature,
        "max_output_tokens": step.max_output_tokens,
        "include_fields": list(step.include_fields),
        "policy": step.policy,
        "injection_screen": step.injection_screen,
        "confidence": {
            "method": step.confidence.method,
            "samples": step.confidence.samples,
            "sample_temperature": step.confidence.sample_temperature,
        },
        "name": defn.name,
        "description": defn.description,
        "outcomes": [
            {"id": o.id, "label": o.label, "description": o.description} for o in defn.outcomes
        ],
        "prompt_version": PROMPT_VERSION,
    }
    return sha256_hex(payload)
