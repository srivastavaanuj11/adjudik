# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is
pre-1.0 and versioned by milestone (SPEC §17).

## [Unreleased]

### M2 — Core model step and engine

- Provider interface and providers (SPEC §7.1-§7.2): `anthropic` (Messages API,
  structured output), `openai_compatible` (Chat Completions, `json_schema` /
  `json_object` / `none`), the `fake` test double, and the deterministic
  `keyword_fake` demo provider with its exact scoring formula. `build_providers`
  reads `ADJUDIK_PROVIDERS`; SDKs are imported lazily and tested over mocked HTTP.
- Data minimisation and the prompt-injection screen (SPEC §7.3-§7.4): untrusted
  strings scanned with the five fixed patterns; any hit gates with
  `suspected_injection` before the model is called.
- Prompt builder with `PROMPT_VERSION` (SPEC §7.5) and the output contract (§7.6):
  case data is escaped so it can never close `<case_data>`; the provider-safe
  schema drops `minimum`/`maximum`/`maxLength` and restates them in descriptions.
- Parsing, fixed error codes, repairs, transport retries with jittered backoff,
  monotonic deadline, a 15-request cap and abort (SPEC §7.6-§7.8), with injectable
  sleep and clock; `NaN`/`Infinity` confidence is rejected and ends in a gate.
- Confidence estimation (SPEC §7.9): `verbalized` and `self_consistency` (samples
  run concurrently; ties break on definition order; too few valid samples fail).
- The model-step fingerprint (SPEC §7.11) with a golden test on the refund example.
- `Engine` wiring the whole lifecycle — rules, minimisation, pre-model gates,
  model step and routing exactly per SPEC §8.2 (blind draws, label propensities,
  model mismatch, audit sampling), with seeded randomness — plus `AutomationPolicy`
  and `EvaluationContext`, emitting the §9.4 audit events. `Engine.evaluate` runs
  end to end with `--provider demo` and no API key.
- CLI: `render-prompt` and `test --with-model [--policy] [--provider]` (SPEC §16.7).

### M1 — Core definitions and rules

- Pydantic models for decision definitions, schema version 1 (SPEC §5.1-§5.5),
  with structural validation and extras forbidden.
- Hardened YAML loader rejecting anchors, aliases, merge keys and >1 MB documents
  (§5.6).
- `validate_definition` implementing every §5.7 rule except rule 11, each issue
  carrying a JSON Pointer `path`, `code`, `message` and `severity`.
- FEEL-lite grammar (verbatim `feel_lite.lark`), LALR parser → immutable AST, and
  evaluator with §6.3 semantics; every §6.2 golden parse string covered, plus
  Hypothesis property tests (print/re-parse round trip, interval semantics,
  `not` as complement).
- Hit policies FIRST/UNIQUE/ANY, conflicts, nullability, and the §6.5 trace.
- Input validation with §5.6 caps (`string_too_long`, `array_too_long`,
  `too_deep`, `integer_out_of_range`) enforced before schema validation.
- Canonical JSON (RFC 8785) with salted and chain hashes (§9.2); committed test
  vectors in `schemas/canonical-json-vectors.json`.
- `definition_hash` and `rules_hash` (notes-excluded) (§5.8); `sample_input`.
- Generated, committed `schemas/decision-definition.schema.json` with a staleness
  test (CI fails if stale).
- `adjudik validate` and rules-only `adjudik test`; both examples
  (`refund_approval`, `support_routing`) with policies, `cases.jsonl`, READMEs,
  and the support-routing sample generator.
- Core coverage 93% (≥90% required).

### M0 — Scaffold

- Monorepo layout per SPEC §4.4: `packages/core`, `server`, `web`, `schemas`,
  `scripts`, `docs`, `examples`.
- uv workspace (`packages/core`, `server`) with shared ruff/mypy/import-linter
  config; `import-linter` contract forbidding `adjudik_core` from importing
  `django`, `rest_framework` or `celery`.
- Django 5.2 + DRF server skeleton with split settings (`base`/`dev`/`prod`/`test`),
  `GET /healthz` (liveness) and `GET /readyz` (DB + Redis readiness) endpoints.
- `adjudik_core` package skeleton with public-API placeholder and framework-free
  import test.
- React 19 + Vite + TypeScript (strict) web skeleton proxying `/api`, `/healthz`,
  `/readyz` to the server.
- `scripts/db-init.sql` creating `adjudik_owner` and `adjudik_app` roles.
- `docker-compose.yml` dev stack (db, redis, server, worker-model,
  worker-webhooks, worker-jobs, beat, web), ports bound to 127.0.0.1.
- `Makefile`, `.env.example`, `README.md`, `LICENSE` (Apache-2.0).
- GitHub Actions CI (`.github/workflows/ci.yml`) running lint, types, security
  scanning and tests, with actions pinned to commit SHAs.
- Initial ADRs: 0001 (record ADRs), 0002 (M0 scaffold choices).
