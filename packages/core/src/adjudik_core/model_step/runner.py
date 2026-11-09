"""Running the model step: retries, deadlines, budgets, repairs (SPEC §7.6-§7.9).

``run_model_step`` never raises for a provider or output error; every failure is
returned as a typed ``ModelStepResult.failure_reason``. Time and sleep are
injected so the whole step is deterministic under test.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..canonical import sha256_hex
from ..definition.models import Definition
from ..providers.base import ModelProvider, ModelRequest, ModelResponse, ProviderError
from . import confidence as conf
from .confidence import SeqResult, SeqSpec, estimator_for
from .parse import parse_attempt
from .prompt import build_prompt, local_output_schema, provider_safe_schema
from .result import CallRecord, ModelStepResult

__all__ = ["run_model_step", "MAX_REQUESTS_PER_STEP"]

MAX_REQUESTS_PER_STEP = 15  # SPEC §7.8
_BACKOFF_BASE = (1.0, 2.0, 4.0)  # seconds, before ±20% jitter
_MAX_BACKOFF = 30.0

_CONFIG_CODES = frozenset({"bad_request", "not_found", "auth"})


def _noop(record: CallRecord) -> None:  # pragma: no cover - default hook
    return None


def run_model_step(
    *,
    defn: Definition,
    minimized: Any,
    provider: ModelProvider,
    deadline: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
    rng: Any = None,
    on_call_start: Callable[[CallRecord], None] = _noop,
    on_call_end: Callable[[CallRecord], None] = _noop,
    should_abort: Callable[[], bool] = lambda: False,
) -> ModelStepResult:
    import random

    runner = _Runner(
        defn=defn,
        minimized=minimized,
        provider=provider,
        deadline=deadline,
        sleep=sleep,
        monotonic=monotonic,
        rng=rng if rng is not None else random.Random(),
        on_call_start=on_call_start,
        on_call_end=on_call_end,
        should_abort=should_abort,
    )
    return runner.run()


class _Runner:
    def __init__(self, **kw: Any) -> None:
        self.defn: Definition = kw["defn"]
        assert self.defn.model_step is not None
        self.step = self.defn.model_step
        self.provider: ModelProvider = kw["provider"]
        self.deadline: float = kw["deadline"]
        self.sleep: Callable[[float], None] = kw["sleep"]
        self.monotonic: Callable[[], float] = kw["monotonic"]
        self.rng = kw["rng"]
        self.on_call_start: Callable[[CallRecord], None] = kw["on_call_start"]
        self.on_call_end: Callable[[CallRecord], None] = kw["on_call_end"]
        self.should_abort: Callable[[], bool] = kw["should_abort"]

        self.outcome_ids = [o.id for o in self.defn.outcomes]
        self.want_confidence = self.step.confidence.method == "verbalized"
        local = local_output_schema(self.defn)
        self.output_schema = provider_safe_schema(local)
        self.system, self.user = build_prompt(self.defn, kw["minimized"])

        self._lock = threading.Lock()
        self._request_count = 0
        self._records: list[CallRecord] = []

    def run(self) -> ModelStepResult:
        estimator = estimator_for(self.step)
        specs = estimator.sequences()
        results: list[SeqResult]
        if len(specs) == 1:
            results = [self._run_sequence(specs[0])]
        else:
            with ThreadPoolExecutor(max_workers=len(specs)) as pool:
                results = list(pool.map(self._run_sequence, specs))

        agg = estimator.aggregate(results, self.outcome_ids)
        records = sorted(
            self._records,
            key=lambda c: (c.sample_index if c.sample_index is not None else -1, c.attempt),
        )
        model_reported = next(
            (c.model_reported for c in records if c.valid and c.model_reported), None
        ) or next((c.model_reported for c in records if c.model_reported), None)

        return ModelStepResult(
            ok=agg.ok,
            outcome=agg.outcome,
            raw_confidence=agg.raw_confidence,
            rationale=agg.rationale,
            rationale_truncated=agg.rationale_truncated,
            valid_samples=agg.valid_samples,
            model_reported=model_reported,
            failure_reason=agg.failure_reason,
            aborted=agg.aborted,
            calls=records,
        )

    def _run_sequence(self, spec: SeqSpec) -> SeqResult:
        attempt = 0
        current_user = self.user
        while True:
            purpose = spec.purpose if attempt == 0 else "repair"
            request = ModelRequest(
                model=self.step.model,
                system=self.system,
                user=current_user,
                output_schema=self.output_schema,
                temperature=spec.temperature,
                max_output_tokens=self.step.max_output_tokens,
                timeout_s=self.step.timeout_s,
            )
            response, status, record = self._request_with_retries(
                request, purpose, spec.sample_index, attempt
            )
            if status != "ok":
                return SeqResult(spec, None, status)

            assert response is not None and record is not None
            parsed = parse_attempt(
                text=response.text,
                stop_reason=response.stop_reason,
                outcome_ids=self.outcome_ids,
                want_confidence=self.want_confidence,
            )
            record.valid = parsed.valid
            record.error_codes = parsed.error_codes
            record.parsed_output = parsed.parsed_output

            if parsed.valid:
                return SeqResult(spec, parsed, conf.VALID)
            if parsed.error_codes == ["refusal"]:
                return SeqResult(spec, parsed, conf.REFUSED)  # never repaired (§7.7)
            if attempt < self.step.max_repair_attempts:
                codes = ", ".join(parsed.error_codes)
                current_user = (
                    f"{self.user}\n\nYour previous reply was rejected: {codes}. "
                    "Reply again with JSON only, following the schema."
                )
                attempt += 1
                continue
            return SeqResult(spec, parsed, conf.INVALID_OUTPUT)

    def _request_with_retries(
        self, request: ModelRequest, purpose: str, sample_index: int | None, attempt: int
    ) -> tuple[ModelResponse | None, str, CallRecord | None]:
        retries_left = self.step.max_retries
        retry_index = 0
        while True:
            if self.should_abort():
                return None, conf.ABORTED, None
            with self._lock:
                if self._request_count >= MAX_REQUESTS_PER_STEP:
                    return None, conf.TRANSPORT, None
                if self.monotonic() + self.step.timeout_s > self.deadline:
                    return None, conf.DEADLINE, None
                self._request_count += 1

            record = CallRecord(
                id=str(uuid.uuid4()),
                purpose=purpose,
                sample_index=sample_index,
                attempt=attempt,
                provider=self.provider.name,
                model_requested=self.step.model,
                request_hash=_request_hash(request),
                system=request.system,
                user=request.user,
            )
            with self._lock:
                self._records.append(record)

            self.on_call_start(record)
            try:
                response = self.provider.complete(request)
            except ProviderError as exc:
                record.provider_error_code = exc.code
                self.on_call_end(record)
                if exc.code in _CONFIG_CODES:
                    return None, conf.CONFIG, record
                if retries_left <= 0:
                    return None, conf.TRANSPORT, record
                self.sleep(self._backoff(retry_index, exc.retry_after_s))
                retries_left -= 1
                retry_index += 1
                continue

            record.model_reported = response.model_reported
            record.response_text = response.text
            record.stop_reason = response.stop_reason
            record.input_tokens = response.input_tokens
            record.output_tokens = response.output_tokens
            record.latency_ms = response.latency_ms
            self.on_call_end(record)
            return response, "ok", record

    def _backoff(self, retry_index: int, retry_after_s: float | None) -> float:
        base = _BACKOFF_BASE[min(retry_index, len(_BACKOFF_BASE) - 1)]
        with self._lock:
            jitter = float(self.rng.uniform(0.8, 1.2))
        delay = min(_MAX_BACKOFF, base * jitter)
        if retry_after_s is not None and retry_after_s > delay:
            delay = min(_MAX_BACKOFF, retry_after_s)
        return delay


def _request_hash(request: ModelRequest) -> str:
    return sha256_hex(
        {
            "model": request.model,
            "system": request.system,
            "user": request.user,
            "output_schema": request.output_schema,
            "temperature": request.temperature,
            "max_output_tokens": request.max_output_tokens,
        }
    )
