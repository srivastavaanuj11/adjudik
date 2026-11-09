# 0002. M0 scaffold choices

Date: 2026-11-09
Status: accepted

## Context

SPEC §4.4 fixes the repository layout and §4.3 the tech stack, but is silent on
several scaffold-level details. Per SPEC §1 rule 4 we record the choices made in
M0.

## Decision

1. **uv workspace root** is a non-published virtual package (`adjudik-workspace`,
   version `0.0.0`) declaring members `packages/core` and `server`. Shared tool
   config (ruff, mypy, import-linter, pytest) lives in the root `pyproject.toml`;
   per-member config overrides where needed.
2. **Settings layout**: `adjudik_server/settings/` as a package with
   `base.py`, `dev.py`, `prod.py`, `test.py` (SPEC §4.4 names these). `base`
   holds shared config; environments import and extend it. `DJANGO_SETTINGS_MODULE`
   defaults to `adjudik_server.settings.dev`.
3. **Health endpoints at the root**: `/healthz` (liveness, no dependency checks)
   and `/readyz` (opens a DB cursor and pings Redis; 503 if either fails) are
   routed outside `/api/v1` (SPEC §11.2), plain Django views, no auth, no DB
   writes.
4. **Config via environment** parsed with `dj-database-url` for DB URLs and
   explicit `os.environ` reads elsewhere; no settings library beyond that, to
   keep dependencies to SPEC §4.3.
5. **import-linter** uses a single `forbidden` contract with
   `root_packages = ["adjudik_core"]`; CI runs `lint-imports`.
6. **CI actions pinned to commit SHAs** (SPEC §4.3) with a trailing `# vX.Y.Z`
   comment for readability; `permissions: contents: read`; no
   `pull_request_target`.

## Alternatives

- `django-environ` / `pydantic-settings` for config — rejected: adds a dependency
  not in §4.3 for little gain at M0.
- A single `settings.py` with env branching — rejected: §4.4 names four modules.
- Pinning CI actions by tag — rejected: §4.3 requires SHAs.

## Consequences

Later milestones extend `settings/base.py` rather than restructuring. Health
endpoints stay dependency-light so liveness never flaps on a slow DB. The strict
framework-free boundary on `adjudik_core` is enforced by both import-linter and a
unit test.
