"""The decision engine: rules, model step and routing wired end to end (SPEC §4.5, §8).

``Engine.evaluate`` runs the whole lifecycle for a single case and returns an
immutable :class:`EvaluationResult` plus a list of :class:`EngineEvent` using the
event types in §9.4. The individual step methods are exposed so the server can
drive the lifecycle incrementally and persist as it goes. The engine is
framework-free: time and randomness are injected.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from .canonical import sha256_hex
from .definition.loader import CompiledDefinition
from .model_step import (
    CallRecord,
    ModelStepResult,
    minimize_input,
    model_step_fingerprint,
    run_model_step,
    screen_flags,
)
from .providers.base import ModelProvider
from .rules.evaluate import RuleResult

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Callable

__all__ = [
    "Clock",
    "SystemClock",
    "EvaluationContext",
    "AutomationPolicy",
    "PreModelResult",
    "AuditSample",
    "Decided",
    "Gate",
    "Routing",
    "ReviewSpec",
    "EngineEvent",
    "EvaluationResult",
    "Engine",
    "PolicyError",
]

WORKER_DEADLINE_S = 90.0  # SPEC §7.8


class Clock(Protocol):
    def now(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)

    def monotonic(self) -> float:
        return time.monotonic()


@dataclass(frozen=True)
class EvaluationContext:
    """Server facts carried into the core (SPEC §4.5)."""

    resubmission: bool = False
    budget_exceeded: bool = False
    observed_model: str | None = None
    blind_rate: float = 0.25


class PolicyError(ValueError):
    """An automation policy is outside the absolute ranges (SPEC §8.1)."""


@dataclass(frozen=True)
class AutomationPolicy:
    """Library-mode automation policy, checked against absolute ranges (SPEC §8.1)."""

    thresholds: dict[str, float | None]
    audit_sample_rate: float
    targets: dict[str, float] | None = None
    calibration_map: list[list[float]] | None = None

    def __post_init__(self) -> None:
        for outcome, threshold in self.thresholds.items():
            if threshold is None:
                continue
            if not (0.0 < threshold <= 1.0):
                raise PolicyError(f"threshold for {outcome!r} must be in (0, 1] or null")
        if not (0.0 <= self.audit_sample_rate <= 0.5):
            raise PolicyError("audit_sample_rate must be in [0, 0.5]")

    @classmethod
    def load(cls, source: str | Path | dict[str, Any]) -> AutomationPolicy:
        from .definition.yaml_loader import load_yaml

        if isinstance(source, dict):
            doc = source
        else:
            path = Path(source)
            doc = load_yaml(path.read_text())
        if not isinstance(doc, dict):
            raise PolicyError("policy must be a mapping")
        thresholds = doc.get("thresholds", {})
        if not isinstance(thresholds, dict):
            raise PolicyError("thresholds must be a mapping")
        return cls(
            thresholds={str(k): v for k, v in thresholds.items()},
            audit_sample_rate=float(doc.get("audit_sample_rate", 0.0)),
            targets=doc.get("targets"),
            calibration_map=doc.get("calibration_map"),
        )

    def calibrated(self, raw: float) -> float | None:
        """Linear interpolation over the calibration map, or ``None`` if absent."""
        points = self.calibration_map
        if not points:
            return None
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        if raw <= xs[0]:
            return ys[0]
        if raw >= xs[-1]:
            return ys[-1]
        for i in range(1, len(xs)):
            if raw <= xs[i]:
                span = xs[i] - xs[i - 1]
                frac = 0.0 if span == 0 else (raw - xs[i - 1]) / span
                return ys[i - 1] + frac * (ys[i] - ys[i - 1])
        return ys[-1]  # pragma: no cover - covered by the >= guard above


@dataclass(frozen=True)
class PreModelResult:
    gate_reason: str | None
    flags: list[dict[str, str]]


@dataclass(frozen=True)
class AuditSample:
    label_propensity: float


@dataclass(frozen=True)
class Decided:
    outcome: str
    by: str  # "rule" | "model"
    raw_confidence: float | None = None
    threshold: float | None = None
    audit: AuditSample | None = None


@dataclass(frozen=True)
class Gate:
    reason: str
    suggestion: str | None = None
    blind: bool = False
    threshold: float | None = None


Routing = Decided | Gate


@dataclass(frozen=True)
class ReviewSpec:
    kind: str  # "gate"
    reason: str
    blind: bool
    label_propensity: float | None
    suggested_outcome: str | None


@dataclass(frozen=True)
class EngineEvent:
    type: str
    occurred_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True)
class EvaluationResult:
    status: str  # "decided" | "needs_review"
    outcome: str | None
    decided_by: str | None  # "rule" | "model" | None
    route_reason: str | None
    rule_result: RuleResult
    screen_flags: list[dict[str, str]]
    model_result: ModelStepResult | None
    review: ReviewSpec | None
    audit_sample: AuditSample | None
    calibrated_confidence: float | None
    events: list[EngineEvent] = field(default_factory=list)


class Engine:
    def __init__(
        self,
        *,
        providers: dict[str, ModelProvider],
        rng: random.Random | None = None,
        clock: Clock | None = None,
        sleep: Callable[[float], None] = time.sleep,
        step_deadline_s: float = WORKER_DEADLINE_S,
    ) -> None:
        self.providers = providers
        self.rng = rng if rng is not None else random.Random()
        self.clock: Clock = clock if clock is not None else SystemClock()
        self.sleep = sleep
        self.step_deadline_s = step_deadline_s

    # --- step functions (SPEC §4.5) ---

    def validate_input(self, defn: CompiledDefinition, data: Any) -> Any:
        return defn.validate_input(data)

    def evaluate_rules(self, defn: CompiledDefinition, data: Any) -> RuleResult:
        result, _trace = defn.evaluate_rules(data)
        return result

    def minimize_input(self, defn: CompiledDefinition, data: Any) -> dict[str, Any]:
        assert defn.definition.model_step is not None
        return minimize_input(defn.input_schema, defn.definition.model_step.include_fields, data)

    def pre_model_check(
        self, defn: CompiledDefinition, minimized: Any, ctx: EvaluationContext
    ) -> PreModelResult:
        assert defn.definition.model_step is not None
        flags: list[dict[str, str]] = []
        if defn.definition.model_step.injection_screen:
            flags = screen_flags(defn.input_schema, minimized)
            if flags:
                return PreModelResult("suspected_injection", flags)
        if ctx.resubmission:
            return PreModelResult("resubmission", flags)
        if ctx.budget_exceeded:
            return PreModelResult("budget_exceeded", flags)
        return PreModelResult(None, flags)

    def run_model_step(
        self,
        defn: CompiledDefinition,
        minimized: Any,
        *,
        deadline: float,
        provider_override: str | None = None,
        on_call_start: Callable[[CallRecord], None] | None = None,
        on_call_end: Callable[[CallRecord], None] | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> ModelStepResult:
        step = defn.definition.model_step
        assert step is not None
        provider = self._resolve_provider(provider_override or step.provider)
        if provider is None:
            return ModelStepResult(
                ok=False,
                outcome=None,
                raw_confidence=None,
                rationale=None,
                rationale_truncated=False,
                valid_samples=None,
                model_reported=None,
                failure_reason="model_invalid_config",
                aborted=False,
                calls=[],
            )
        hooks: dict[str, Any] = {}
        if on_call_start is not None:
            hooks["on_call_start"] = on_call_start
        if on_call_end is not None:
            hooks["on_call_end"] = on_call_end
        if should_abort is not None:
            hooks["should_abort"] = should_abort
        return run_model_step(
            defn=defn.definition,
            minimized=minimized,
            provider=provider,
            deadline=deadline,
            sleep=self.sleep,
            monotonic=self.clock.monotonic,
            rng=self.rng,
            **hooks,
        )

    def route(
        self,
        defn: CompiledDefinition,
        rule_result: RuleResult,
        model_result: ModelStepResult | None,
        policy: AutomationPolicy | None,
        ctx: EvaluationContext,
    ) -> Routing:
        return _route(defn.definition, rule_result, model_result, policy, ctx, self.rng)

    def fingerprint(self, defn: CompiledDefinition, provider_override: str | None = None) -> str:
        step = defn.definition.model_step
        assert step is not None
        provider = self._resolve_provider(provider_override or step.provider)
        if provider is None:
            raise PolicyError(f"provider {provider_override or step.provider!r} is not configured")
        return model_step_fingerprint(defn.definition, provider)

    # --- end-to-end evaluation (SPEC §4.5) ---

    def evaluate(
        self,
        defn: CompiledDefinition,
        data: Any,
        *,
        policy: AutomationPolicy | None = None,
        ctx: EvaluationContext | None = None,
        provider_override: str | None = None,
        should_abort: Callable[[], bool] | None = None,
    ) -> EvaluationResult:
        ctx = ctx or EvaluationContext()
        events: list[EngineEvent] = []

        defn.validate_input(data)  # raises InputValidationError

        events.append(
            self._event(
                "evaluation.received",
                {
                    "decision_version_id": None,
                    "input_hash": sha256_hex(data),
                    "minimized_input_hash": None,
                    "has_external_id": False,
                    "idempotency_key_present": False,
                },
            )
        )

        rule_result, _trace = defn.evaluate_rules(data)
        events.append(
            self._event(
                "rules.evaluated",
                {
                    "type": rule_result.type,
                    "matched_row_ids": rule_result.matched_row_ids,
                    "row_id": rule_result.row_id,
                    "outcome": rule_result.outcome,
                },
            )
        )

        model_result: ModelStepResult | None = None
        screen: list[dict[str, str]] = []

        if _rule_handled_without_model(defn.definition, rule_result):
            routing = _route(defn.definition, rule_result, None, policy, ctx, self.rng)
        else:
            minimized = self.minimize_input(defn, data)
            pre = self.pre_model_check(defn, minimized, ctx)
            screen = pre.flags
            if pre.flags:
                events.append(
                    self._event(
                        "model.screen_flagged",
                        {
                            "rules": sorted({f["rule"] for f in pre.flags}),
                            "paths": [f["path"] for f in pre.flags],
                        },
                    )
                )
            if pre.gate_reason is not None:
                routing = Gate(pre.gate_reason)
            else:
                model_result = self.run_model_step(
                    defn,
                    minimized,
                    deadline=self.clock.monotonic() + self.step_deadline_s,
                    provider_override=provider_override,
                    should_abort=should_abort,
                )
                self._emit_model_events(events, model_result)
                routing = _route(defn.definition, rule_result, model_result, policy, ctx, self.rng)

        return self._assemble(defn, rule_result, screen, model_result, routing, policy, ctx, events)

    # --- helpers ---

    def _resolve_provider(self, name: str) -> ModelProvider | None:
        return self.providers.get(name)

    def _event(self, event_type: str, payload: dict[str, Any]) -> EngineEvent:
        return EngineEvent(type=event_type, occurred_at=self.clock.now(), payload=payload)

    def _emit_model_events(self, events: list[EngineEvent], model_result: ModelStepResult) -> None:
        for call in model_result.calls:
            events.append(
                self._event(
                    "model.called",
                    {
                        "model_call_id": call.id,
                        "purpose": call.purpose,
                        "sample_index": call.sample_index,
                        "attempt": call.attempt,
                        "provider": call.provider,
                        "model_requested": call.model_requested,
                        "request_hash": call.request_hash,
                    },
                )
            )
            events.append(
                self._event(
                    "model.responded",
                    {
                        "model_call_id": call.id,
                        "valid": call.valid,
                        "stop_reason": call.stop_reason,
                        "error_codes": call.error_codes,
                        "provider_error_code": call.provider_error_code,
                        "outcome": (call.parsed_output or {}).get("outcome"),
                        "raw_confidence": (call.parsed_output or {}).get("confidence"),
                        "model_reported": call.model_reported,
                        "response_hash": (
                            sha256_hex(call.response_text)
                            if call.response_text is not None
                            else None
                        ),
                        "input_tokens": call.input_tokens,
                        "output_tokens": call.output_tokens,
                        "latency_ms": call.latency_ms,
                    },
                )
            )
        if not model_result.ok:
            events.append(
                self._event(
                    "model.step_failed",
                    {
                        "failure_reason": model_result.failure_reason,
                        "aborted": model_result.aborted,
                    },
                )
            )

    def _assemble(
        self,
        defn: CompiledDefinition,
        rule_result: RuleResult,
        screen: list[dict[str, str]],
        model_result: ModelStepResult | None,
        routing: Routing,
        policy: AutomationPolicy | None,
        ctx: EvaluationContext,
        events: list[EngineEvent],
    ) -> EvaluationResult:
        raw = model_result.raw_confidence if model_result is not None else None
        calibrated = policy.calibrated(raw) if (policy is not None and raw is not None) else None

        if isinstance(routing, Decided):
            events.append(
                self._event(
                    "evaluation.routed",
                    {
                        "route": "auto",
                        "decided_by": routing.by,
                        "outcome": routing.outcome,
                        "suggested_outcome": None,
                        "raw_confidence": routing.raw_confidence,
                        "calibrated_confidence": calibrated,
                        "threshold": routing.threshold,
                        "automation_policy_id": None,
                        "reason": None,
                        "blind": False,
                        "audit_sampled": routing.audit is not None,
                        "rationale_hash": _rationale_hash(model_result),
                    },
                )
            )
            events.append(
                self._event(
                    "evaluation.decided",
                    {"outcome": routing.outcome, "decided_by": routing.by, "state_version": 1},
                )
            )
            return EvaluationResult(
                status="decided",
                outcome=routing.outcome,
                decided_by=routing.by,
                route_reason=None,
                rule_result=rule_result,
                screen_flags=screen,
                model_result=model_result,
                review=None,
                audit_sample=routing.audit,
                calibrated_confidence=calibrated,
                events=events,
            )

        label_propensity = ctx.blind_rate if routing.blind else None
        review = ReviewSpec(
            kind="gate",
            reason=routing.reason,
            blind=routing.blind,
            label_propensity=label_propensity,
            suggested_outcome=routing.suggestion,
        )
        events.append(
            self._event(
                "evaluation.routed",
                {
                    "route": "review",
                    "decided_by": None,
                    "outcome": None,
                    "suggested_outcome": routing.suggestion,
                    "raw_confidence": raw,
                    "calibrated_confidence": calibrated,
                    "threshold": routing.threshold,
                    "automation_policy_id": None,
                    "reason": routing.reason,
                    "blind": routing.blind,
                    "audit_sampled": False,
                    "rationale_hash": _rationale_hash(model_result),
                },
            )
        )
        events.append(
            self._event(
                "review.created",
                {
                    "review_task_id": None,
                    "kind": "gate",
                    "reason": routing.reason,
                    "blind": routing.blind,
                    "label_propensity": label_propensity,
                    "suggested_outcome": routing.suggestion,
                },
            )
        )
        return EvaluationResult(
            status="needs_review",
            outcome=None,
            decided_by=None,
            route_reason=routing.reason,
            rule_result=rule_result,
            screen_flags=screen,
            model_result=model_result,
            review=review,
            audit_sample=None,
            calibrated_confidence=calibrated,
            events=events,
        )


def _rule_handled_without_model(defn: Any, rule_result: RuleResult) -> bool:
    if rule_result.type in ("decide", "review", "conflict"):
        return True
    on_no_match = defn.rules.on_no_match if defn.rules is not None else "model"
    return rule_result.type == "no_match" and on_no_match == "review"


def _rationale_hash(model_result: ModelStepResult | None) -> str | None:
    if model_result is None or model_result.rationale is None:
        return None
    return sha256_hex(model_result.rationale)


def _route(
    defn: Any,
    rule: RuleResult,
    model: ModelStepResult | None,
    policy: AutomationPolicy | None,
    ctx: EvaluationContext,
    rng: random.Random,
) -> Routing:
    """Routing algorithm, verbatim from SPEC §8.2."""
    import math

    def draw(b: float) -> bool:
        return rng.random() < b

    if rule.type == "decide":
        assert rule.outcome is not None
        return Decided(outcome=rule.outcome, by="rule")
    if rule.type == "review":
        return Gate("rule_forced_review")
    if rule.type == "conflict":
        return Gate("rule_conflict")
    on_no_match = defn.rules.on_no_match if defn.rules is not None else "model"
    if rule.type == "no_match" and on_no_match == "review":
        return Gate("no_rule_matched")

    # Handed to the model. Pre-model gates were checked before any model call.
    assert model is not None
    if not model.ok:
        assert model.failure_reason is not None
        return Gate(model.failure_reason)
    o, s = model.outcome, model.raw_confidence
    assert o is not None and s is not None
    if ctx.observed_model and model.model_reported != ctx.observed_model:
        return Gate("model_mismatch", suggestion=o)
    if policy is None:
        return Gate("automation_disabled", suggestion=o, blind=draw(ctx.blind_rate))
    if o in defn.routing.always_review_outcomes:
        return Gate("always_review_outcome", suggestion=o, blind=draw(ctx.blind_rate))
    t = policy.thresholds.get(o)
    if t is None:
        return Gate("outcome_not_automated", suggestion=o, blind=draw(ctx.blind_rate))
    if not (math.isfinite(s) and s >= t):  # fail closed
        return Gate("low_confidence", suggestion=o, threshold=t, blind=draw(ctx.blind_rate))
    audit = (
        AuditSample(label_propensity=policy.audit_sample_rate)
        if rng.random() < policy.audit_sample_rate
        else None
    )
    return Decided(outcome=o, by="model", raw_confidence=s, threshold=t, audit=audit)
