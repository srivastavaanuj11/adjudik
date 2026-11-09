"""Confidence estimation strategies (SPEC §7.9).

Each estimator declares the sequence of calls to run and how to aggregate their
parsed results into an outcome and a raw confidence. New methods can be added by
implementing the ``ConfidenceEstimator`` protocol.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from ..definition.models import ModelStep
from .parse import ParsedAttempt

__all__ = [
    "SeqSpec",
    "SeqResult",
    "Aggregate",
    "ConfidenceEstimator",
    "VerbalizedEstimator",
    "SelfConsistencyEstimator",
    "estimator_for",
]

# Per-sequence result kinds.
VALID = "valid"
INVALID_OUTPUT = "invalid_output"
TRANSPORT = "transport"
REFUSED = "refused"
CONFIG = "config"
ABORTED = "aborted"
DEADLINE = "deadline"


@dataclass(frozen=True)
class SeqSpec:
    purpose: str  # "primary" | "sample"
    sample_index: int | None
    temperature: float | None


@dataclass(frozen=True)
class SeqResult:
    spec: SeqSpec
    parsed: ParsedAttempt | None
    kind: str


@dataclass(frozen=True)
class Aggregate:
    ok: bool
    outcome: str | None
    raw_confidence: float | None
    rationale: str | None
    rationale_truncated: bool
    valid_samples: int | None
    failure_reason: str | None
    aborted: bool


_KIND_TO_FAILURE = {
    TRANSPORT: "model_unavailable",
    REFUSED: "model_refused",
    INVALID_OUTPUT: "model_invalid_output",
    CONFIG: "model_invalid_config",
    ABORTED: "model_unavailable",
    DEADLINE: "model_unavailable",
}


class ConfidenceEstimator(Protocol):
    k: int

    def sequences(self) -> list[SeqSpec]: ...

    def aggregate(self, results: list[SeqResult], outcome_order: list[str]) -> Aggregate: ...


class VerbalizedEstimator:
    def __init__(self, step: ModelStep) -> None:
        self.k = 1
        self._temperature = step.temperature

    def sequences(self) -> list[SeqSpec]:
        return [SeqSpec(purpose="primary", sample_index=None, temperature=self._temperature)]

    def aggregate(self, results: list[SeqResult], outcome_order: list[str]) -> Aggregate:
        result = results[0]
        if result.kind == VALID:
            assert result.parsed is not None
            return Aggregate(
                ok=True,
                outcome=result.parsed.outcome,
                raw_confidence=result.parsed.confidence,
                rationale=result.parsed.rationale,
                rationale_truncated=result.parsed.rationale_truncated,
                valid_samples=None,
                failure_reason=None,
                aborted=False,
            )
        return Aggregate(
            ok=False,
            outcome=None,
            raw_confidence=None,
            rationale=None,
            rationale_truncated=False,
            valid_samples=None,
            failure_reason=_KIND_TO_FAILURE[result.kind],
            aborted=result.kind == ABORTED,
        )


class SelfConsistencyEstimator:
    def __init__(self, step: ModelStep) -> None:
        self.k = step.confidence.samples
        self._sample_temperature = step.confidence.sample_temperature

    def sequences(self) -> list[SeqSpec]:
        return [
            SeqSpec(purpose="sample", sample_index=i, temperature=self._sample_temperature)
            for i in range(self.k)
        ]

    def aggregate(self, results: list[SeqResult], outcome_order: list[str]) -> Aggregate:
        ordered = sorted(results, key=lambda r: r.spec.sample_index or 0)
        valid = [r for r in ordered if r.kind == VALID and r.parsed is not None]
        valid_samples = len(valid)

        if any(r.kind == CONFIG for r in ordered):
            return self._fail("model_invalid_config", valid_samples, aborted=False)

        if valid_samples >= math.ceil(self.k / 2):
            counts: dict[str, int] = {}
            for r in valid:
                assert r.parsed is not None and r.parsed.outcome is not None
                counts[r.parsed.outcome] = counts.get(r.parsed.outcome, 0) + 1
            max_count = max(counts.values())
            winner = next(o for o in outcome_order if counts.get(o, 0) == max_count)
            first = next(r for r in valid if r.parsed is not None and r.parsed.outcome == winner)
            assert first.parsed is not None
            return Aggregate(
                ok=True,
                outcome=winner,
                raw_confidence=max_count / self.k,
                rationale=first.parsed.rationale,
                rationale_truncated=first.parsed.rationale_truncated,
                valid_samples=valid_samples,
                failure_reason=None,
                aborted=False,
            )

        # Too few valid samples: unavailable if the shortfall is mostly transport.
        invalid = self.k - valid_samples
        unavailable_like = sum(1 for r in ordered if r.kind in (TRANSPORT, ABORTED, DEADLINE))
        aborted = any(r.kind == ABORTED for r in ordered)
        if unavailable_like * 2 > invalid:
            return self._fail("model_unavailable", valid_samples, aborted=aborted)
        return self._fail("model_invalid_output", valid_samples, aborted=aborted)

    @staticmethod
    def _fail(reason: str, valid_samples: int, *, aborted: bool) -> Aggregate:
        return Aggregate(
            ok=False,
            outcome=None,
            raw_confidence=None,
            rationale=None,
            rationale_truncated=False,
            valid_samples=valid_samples,
            failure_reason=reason,
            aborted=aborted,
        )


def estimator_for(step: ModelStep) -> ConfidenceEstimator:
    if step.confidence.method == "self_consistency":
        return SelfConsistencyEstimator(step)
    return VerbalizedEstimator(step)
