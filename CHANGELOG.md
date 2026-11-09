# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is
pre-1.0 and versioned by milestone (SPEC §17).

## [Unreleased]

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
