# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); the project is
pre-1.0 and versioned by milestone (SPEC §17).

## [Unreleased]

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
