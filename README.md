# Adjudik

Adjudik is an open-source decision engine that settles routine business decisions
with rules, sends judgment cases to an LLM, auto-decides only where an admin has
switched automation on, sends everything else to a human, and logs every step in a
tamper-evident record. See [`docs/SPEC.md`](docs/SPEC.md) for the full specification
and [`docs/PLAN.md`](docs/PLAN.md) for the milestone checklist.

> **Status: M0 (scaffold).** The repository, services and tooling are in place;
> decision logic (rules, model step, engine, API) lands in M1 onward. Endpoints
> other than `/healthz` and `/readyz` are not implemented yet.

## Architecture

Adjudik is a monorepo with three deliverables (SPEC §4):

| Component | What it is |
| --- | --- |
| `adjudik_core` (`packages/core`, PyPI `adjudik`) | Django-free Python engine: definition loading/validation, FEEL-lite rules, injection screen, prompt building, model step, routing, calibration/replay maths, canonical JSON + chain verification, CLI. Imports no Django/DRF/Celery. |
| Server (`server`, Django 5.2 + DRF) | REST API under `/api/v1`, auth, persistence, review queue, audit log, webhook outbox, metrics. Calls the core for every decision step. |
| Web (`web`, React 19 + Vite + TS) | Dashboard SPA. Vite dev server in development; built static files served by Django via WhiteNoise in production. |

Backing services: PostgreSQL 16 (system of record), Redis 7 (Celery broker,
throttles, token budgets, pub/sub wake-ups). Workers run under Celery, one queue
each (`model`, `webhooks`, `jobs`, `default`).

```
packages/core   server   web
       \          |        /
        \      Postgres   /
         \      Redis    /
          LLM providers
```

## Prerequisites

- Docker + Docker Compose
- [uv](https://docs.astral.sh/uv/) (Python 3.12 toolchain)
- Node 22 LTS + npm

## Install

```bash
make setup     # uv sync --locked, npm ci in web/, create .env from .env.example
```

## Quickstart

Start the whole stack (Postgres, Redis, Django, Celery workers, beat, Vite):

```bash
make dev
```

Then:

- Dashboard (dev): http://localhost:5173
- API: http://localhost:8000/api/v1
- Liveness: `curl http://localhost:8000/healthz`
- Readiness (DB + Redis): `curl http://localhost:8000/readyz`

Load demo data once the engine exists (M3b onward):

```bash
make seed
```

### Using the core library (available from M1)

```python
import random
from adjudik_core import Engine, EvaluationContext, load_definition
from adjudik_core.providers import build_providers

defn = load_definition("examples/refund_approval/decision.yaml")
engine = Engine(providers=build_providers(), rng=random.Random())
# result = engine.evaluate(defn, input_data, policy=None)
```

The built-in `demo` provider needs no API key, so the quickstart runs offline.

## Development

| Command | Does |
| --- | --- |
| `make setup` | Install Python + web deps, create `.env` |
| `make dev` | `docker compose up` (full stack) |
| `make seed` | Load demo data (SPEC §16.5) |
| `make test` | Core, server and web unit tests |
| `make e2e` | Playwright against the compose stack |
| `make lint` / `make fmt` | Lint + type + security checks / auto-format |
| `make openapi` | Regenerate `schemas/openapi.json` and the web client |

Database roles (SPEC §10.3): `scripts/db-init.sql` creates `adjudik_owner`
(migrations and tests, via `DATABASE_OWNER_URL`) and `adjudik_app` (runtime,
via `DATABASE_URL`, least privilege). Migrations always run as the owner.

## License

[Apache-2.0](LICENSE).
