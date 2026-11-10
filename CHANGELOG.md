# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is
pre-1.0 and versioned by milestone (SPEC §17).

## [Unreleased]

### M3a — Server foundation

- Full §10 data model and migrations across `accounts`, `decisions`,
  `evaluations`, `reviews`, `audit` and `webhooks`: UUID4 PKs (bigint identity
  for `audit_event`), DB-level composite workspace foreign keys
  `(workspace_id, parent_id)`, partial unique indexes (one active version per
  decision, one active policy per version, idempotency key per decision), the
  §10.3 grant matrix and the §9.6 immutability triggers (append-only
  `audit_event`, frozen published definitions and terminal evaluations/reviews).
- Audit append service (SPEC §9.3): `append_many` locks the chain head, assigns
  gapless seqs and chain hashes, and enforces the one-append-per-transaction
  rule; covered by the one-append and 32-writer concurrency tests, and by
  append-only checks as both the owner and `adjudik_app` roles.
- Core `audit_chain` (framework-free): `verify`, Ed25519 checkpoint
  signing/verification, and `adjudik audit verify` with truncation detection.
- Auth and accounts (SPEC §15.2, §16.6): `adjudik_bootstrap`; session and
  `Bearer adk_…` API-key authentication; capability permissions
  (`is_staff`/`is_superuser` never consulted); members; API-key
  create/expire/rotate (24 h grace)/revoke; login throttle with doubling
  lockouts; first-login password change; 8 h/30 min session lifetimes;
  workspace settings with floors.
- Decisions and versions (SPEC §11.2, §11.8): YAML and JSON draft upload,
  validate, multiple drafts, publish (rule 11 provider check, four-eyes, and the
  model-step fingerprint computed at publish), retire with policy deactivation,
  policy carry-over, dry-run, render-prompt, and acknowledge-model.
- Manual automation policies (SPEC §8.1): propose (activates at once unless
  four-eyes), approve by a different admin, reject and deactivate, checked
  against the `min_threshold` and `min_audit_sample_rate` floors.
- Evaluate (SPEC §11.3/§11.4), the model worker and the sweeper (§11.5): rule
  path returns 200, the model path 200/202; idempotency replay/conflict with the
  concurrent-race test; the injection, resubmission and budget pre-model gates;
  the fencing-token guard with its race test; and the Redis-backed result wait.
  The server drives the core step functions and builds its own audit events with
  salted content hashes and real row ids.
- Audit operations (SPEC §9.5/§9.6): hourly signed checkpoints (optional
  anchor POST), the three verification modes (incremental, full, reconcile) as a
  job recording `audit_verification`, and NDJSON export ending in an export-time
  checkpoint; `adjudik audit verify` accepts a good export and rejects tampered
  or truncated ones.
- The §15.1 permission matrix and the two-workspace isolation test pass for
  every endpoint built so far.

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
