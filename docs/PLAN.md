# Adjudik implementation plan

This is the milestone checklist from [SPEC.md](SPEC.md) §17, built strictly in order.
A milestone is not started until the previous one's boxes all pass. Each milestone
ends with: tests green, lint/type/security checks green, README updated, a
`CHANGELOG.md` entry, and a summary (built / deviated / known gaps).

Precedence: SPEC §1.1 amendments override conflicting prose; §10 (data model) and
§11 (API) win over prose elsewhere. When the spec is silent, pick the simplest
consistent option (most restrictive for security/privacy) and record an ADR under
`docs/decisions/`.

Legend: `[ ]` todo · `[~]` in progress · `[x]` done.

---

## M0 — Scaffold  (SPEC §4.4, §16, §17)  — COMPLETE

- [x] Repository layout from §4.4; `make setup` works (uv.lock + package-lock.json committed).
- [x] `make dev` services defined; `GET /healthz` → 200; `GET /readyz` → 200 when DB+Redis up, 503 when either down (tested).
- [x] Two DB roles from `scripts/db-init.sql`; migrations run as the owner (verified: test DB created by `adjudik_owner`).
- [x] CI runs lint, types, security scanning and tests on PRs, actions pinned to SHAs (workflow committed; runs on first push to GitHub).
- [x] import-linter contract forbids `adjudik_core` importing `django`, `rest_framework`, `celery` (contract + subprocess test, both green).

## M1 — Core definitions and rules  (SPEC §5, §6)  — COMPLETE

- [x] Pydantic models for schema version 1; `schemas/decision-definition.schema.json` generated + committed; CI fails if stale (test_schema_committed.py).
- [x] Input limits (§5.6), alias-rejecting YAML loader, every §5.7 rule except rule 11 — each tested, with a JSON Pointer path.
- [x] FEEL-lite per §6.2/§6.3 incl. every golden string and property tests.
- [x] Hit policies, nullability, conflicts (§6.1/§6.4); trace (§6.5, exact match test).
- [x] `definition_hash`, `rules_hash`; JCS canonical JSON passing committed vectors.
- [x] `adjudik validate` and `adjudik test` (rules only) pass on both examples; refund `cases.jsonl` = 17 cases.
- [x] Core coverage 93% (≥ 90%).

## M2 — Core model step and engine  (SPEC §7, §8.2)

- [ ] Providers per §7.2, mocked HTTP only, incl. `keyword_fake` formula.
- [ ] Minimisation, injection screen (±1 test per rule), built-in prompt golden test; `</case_data>` escaped.
- [ ] Parsing, error codes, repairs, retries, deadline, request cap, abort (§7.6–§7.8); `NaN` confidence → gate.
- [ ] `verbalized` and `self_consistency` (§7.9) incl. ties and too-few samples.
- [ ] Routing per §8.2 incl. blind draws, label propensities, pre-model gates, model mismatch; seeded RNG.
- [ ] Fingerprint (§7.11) golden test.
- [ ] `Engine.evaluate` end to end with `--provider demo`; README quickstart runs with no API key; `render-prompt`, `test --with-model --policy`.

## M3a — Server foundation  (SPEC §9–§11, §15)

- [ ] Models/migrations per §10: composite workspace FKs, partial unique indexes, grants, triggers; `UPDATE` on `audit_event` fails as both roles; non-draft definitions and terminal evaluations cannot update.
- [ ] `adjudik_bootstrap`; session + API-key auth; members; login throttle, password validators, session lifetimes; API-key expiry and rotation.
- [ ] Permission test + two-workspace isolation test (§15.1) for every endpoint so far.
- [ ] Decisions/versions: YAML+JSON upload, validate, multi-draft, publish (rule 11), retire, carry-over, dry-run, render-prompt.
- [ ] Workspace settings + manual automation policies with floors.
- [ ] Evaluate (§11.3/§11.4); worker + sweeper (§11.5): rule-only 200; model path 200/202; idempotency incl. concurrent test; resubmission/injection/budget gates; fencing race test.
- [ ] Audit chain (§9.2/§9.3): head-row append, one-append rule, 32-writer concurrency; hourly checkpoints; verification 3 modes; export + `adjudik audit verify`.

## M3b — Server operations  (SPEC §8, §9.7, §11.6, §11.7)

- [ ] Review endpoints (§8.5): parallel `next` test, lease expiry, one-claim-per-reviewer, reason codes, blind redaction.
- [ ] Cancel (§11.2, creating key only) + cancel-vs-complete race.
- [ ] Webhooks (§11.6): outbox in same txn, dispatcher, retry schedule, signatures with rotation, thin payload, blocked-address (DNS change) test.
- [ ] Metrics (§11.7) against a deterministic fixture.
- [ ] Retention, purge, legal hold, salt destruction (§9.7); chain still verifies + reconcile passes after purge.
- [ ] Every periodic job (§8.7) tested; skips when advisory lock held.
- [ ] Throttles → 429 + `Retry-After`; CSRF enforced; logging canary.
- [ ] `settings.prod` refuses to start for each §15.7 condition (one test each).
- [ ] `seed_demo` (§16.5) incl. calibration-readiness assertion; OpenAPI generates without warnings.

## M4 — Web dashboard  (SPEC §12, §16.2)

- [ ] All §12.1 screens except calibration/replay tabs, role-based nav.
- [ ] Review workspace (§12.2); component test: blind never renders a suggestion; visible suggestions never show confidence.
- [ ] Playwright: reviewer completes 3 reviews by keyboard; auditor sees `decided_by: human` + timeline.
- [ ] Playwright: admin proposes a manual policy.
- [ ] `Dockerfile` + `docker-compose.prod.yml` (§16.2) build and serve dashboard + API, incl. SPA deep links.

## M5 — Calibration  (SPEC §13, §8.8)

- [ ] `stats.py`: Clopper-Pearson vs `scipy.stats.beta.ppf` to 1e-6; weighted PAV.
- [ ] `calibration.py` (§13.2–§13.5).
- [ ] Simulation test (3 scenarios, 1,000 seeded runs each) within stated error bounds.
- [ ] Propensity test (weighted vs unweighted).
- [ ] Server: run/list/get/apply endpoints; jobs; applied policies drive routing; `calibrated_confidence` filled; carry-over checks both hashes.
- [ ] Circuit breaker (§8.8) test.
- [ ] Calibration tab (§12.3); Playwright apply at 0.95.
- [ ] `adjudik bench` on 5,000-ticket sample.

## M6 — Replay  (SPEC §14)

- [ ] Replay jobs (§14): batching, budget sampling, model-result reuse, provider semaphore.
- [ ] Isolation test: evaluation/review/model-call/webhook row counts unchanged by a replay.
- [ ] Summary (§14.5) vs hand-computed values incl. McNemar interval.
- [ ] Replays tab (§12.3); Playwright: draft that changes one rule, replay 30 days, see changed cases.

---

## Open questions for the owner (SPEC §19)

- License: Apache-2.0 assumed. Switch to MIT?
- Reviewers see full input except hidden fields — default to `include_fields` only instead?
- Hosted public demo in scope later?
