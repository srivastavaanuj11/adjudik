# Adjudik

Adjudik is an open-source decision engine that settles routine business decisions
with rules, sends judgment cases to an LLM, auto-decides only where an admin has
switched automation on, sends everything else to a human, and logs every step in a
tamper-evident record. See [`docs/SPEC.md`](docs/SPEC.md) for the full specification
and [`docs/PLAN.md`](docs/PLAN.md) for the milestone checklist.

> **Status: M3b (server operations).** The framework-free core loads and
> validates decision definitions, evaluates FEEL-lite rules, runs the LLM model
> step (providers, minimisation, injection screen, prompt, parsing, repairs,
> retries and confidence estimation), and routes every case per the spec, with a
> deterministic `demo` provider. The Django server adds the §10 data model,
> session and API-key auth, decisions/versions (upload, validate, publish,
> retire, carry-over, dry-run, render-prompt, acknowledge-model), manual
> automation policies, the evaluate endpoint with its model worker and sweeper,
> and the tamper-evident audit log (per-workspace hash chain, hourly signed
> checkpoints, verification and NDJSON export). M3b adds the review queue
> (claim/heartbeat/release/complete with leases and blind redaction), evaluation
> cancel, SSRF-safe webhooks with signed retries, decision metrics, data
> retention/purge/legal-hold, the periodic jobs, per-key/workspace throttles,
> CSRF, the production hardening checks and `seed_demo`. The web dashboard (M4)
> and calibration/replay (M5/M6) are not implemented yet.

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

### Using the core library

The whole lifecycle — rules, model step and routing — runs through `Engine`. The
built-in `demo` provider is deterministic and needs no API key:

```python
import random

from adjudik_core import Engine, load_definition
from adjudik_core.providers import build_providers

defn = load_definition("examples/refund_approval/decision.yaml")
engine = Engine(providers=build_providers(), rng=random.Random(0))

# A case the rules hand to the model (mid-value, not covered by a row):
case = {
    "order": {
        "id": "A-12",
        "amount": 120.0,
        "currency": "USD",
        "status": "delivered",
        "days_since_delivery": 4,
    },
    "customer": {"id": "C-12", "tier": "standard", "refunds_last_90d": 0},
    "request": {"reason_code": "damaged", "reason_text": "the item arrived damaged and broken"},
}

result = engine.evaluate(defn, case, provider_override="demo")
print(result.status)  # needs_review (no automation policy)
print(result.route_reason)  # automation_disabled
print(result.review.suggested_outcome)  # approve
```

With an admin-approved automation policy whose threshold the model's confidence
meets, the same case is auto-decided:

```python
from adjudik_core import AutomationPolicy

policy = AutomationPolicy(thresholds={"approve": 0.5, "deny": 0.5}, audit_sample_rate=0.05)
result = engine.evaluate(defn, case, policy=policy, provider_override="demo")
print(result.status, result.decided_by)  # decided model
```

Rule-decided cases never call the model, and `result.events` holds the audit
events (SPEC §9.4) for the run.

From the CLI (no API key needed with `--provider demo`):

```bash
adjudik validate examples/refund_approval/decision.yaml
adjudik test examples/refund_approval/decision.yaml --cases examples/refund_approval/cases.jsonl --with-model --provider demo
adjudik render-prompt examples/refund_approval/decision.yaml --input case.json
```

Real providers (`anthropic`, `openai_compatible`) are configured through the
`ADJUDIK_PROVIDERS` environment variable and read their keys from the environment;
install the matching extra (`pip install adjudik[anthropic]`).

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
