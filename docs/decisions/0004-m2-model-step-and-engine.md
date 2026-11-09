# 0004. M2 model-step and engine choices

Date: 2026-11-09
Status: accepted

## Context

SPEC §7 (model step), §8 (routing) and §4.5 (public API) fix most behaviour, but
leave some mechanics to the implementer, and the installed provider SDKs differ
from what the spec assumed. Per SPEC §1 rule 4 we record the choices.

## Decision

1. **Gate reason `no_rule_matched`.** §8.2's `route()` returns
   `Gate("no_rule_matched")` for a no-match under `on_no_match: review`. M1's ADR
   0003 had named this route reason `no_match_review` before the engine existed.
   We reconcile to the spec: the engine and the rules-only `adjudik test` now both
   emit `no_rule_matched`. `rule_forced_review` and `rule_conflict` are unchanged.

2. **Provider SDKs are a dev dependency; the installed Anthropic SDK uses
   `httpx2`.** `anthropic` and `openai` stay runtime-optional extras of `adjudik`,
   but are added to the dev dependency group so the model-step provider tests run
   over mocked HTTP in CI. The pinned `anthropic` SDK bundles a fork (`httpx2`) and
   its `messages.create` has no top-level `temperature`, so temperature is sent via
   `extra_body` — reaching the API rather than being dropped silently (§7.8).
   Mocked-HTTP tests inject an `httpx2.MockTransport` (Anthropic) or
   `httpx.MockTransport` (OpenAI) client.

3. **Provider error-code mapping.** `timeout`/`rate_limited`/`server_error`/
   `connection` are retryable; `bad_request`/`not_found`/`auth` are not. A
   non-retryable error ends the step with `model_invalid_config` — `auth` included,
   since a bad or missing key is a configuration problem.

4. **Events are assembled in `evaluate()`; library-mode hashes are unsalted.** The
   §4.5 step methods return their annotated result types (`RuleResult`, `dict`,
   `PreModelResult`, `ModelStepResult`, `Routing`); `Engine.evaluate` assembles the
   `EngineEvent` list using the §9.4 event types. In library mode, content hashes
   (`input_hash`, `rationale_hash`, `response_hash`) are plain SHA-256 over
   canonical JSON and ids are `None`; the server supplies salted hashes and real
   row ids when it drives the steps in M3. `model_reported` on the step result is
   the first valid call's reported model.

5. **Confidence settings in the fingerprint; estimator protocol.** The fingerprint
   (§7.11) includes `method`, `samples` and `sample_temperature` for stability
   across methods. Estimators implement a `ConfidenceEstimator` protocol
   (`sequences()` + `aggregate()`) so new methods can be added (§7.9).

6. **`keyword_fake` token counts** are a coarse `len(text)//4` stand-in; the demo
   provider has no real tokeniser and ships no prices, so cost is null (§7.2).

## Alternatives

- Implementing providers over raw `httpx` instead of the official SDKs — rejected:
  §7.2 mandates the official SDKs; we mock at the transport layer instead.
- Dropping `temperature` when the SDK lacks the parameter — rejected by §7.8
  ("parameters are never dropped silently"); we pass it through `extra_body`.

## Consequences

The route-reason and provider-error-code vocabularies are now consumed by the
engine and CLI and will be consumed by the server (M3) and dashboard (M4);
changing them is a breaking change needing a new ADR. The event payloads emitted
in library mode will be extended (salted hashes, persisted ids) by the server in
M3 without changing the event types. See [[0003-m1-core-choices]].
