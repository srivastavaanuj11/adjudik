# 0005. M3a server-foundation choices

Date: 2026-11-10
Status: accepted

## Context

SPEC §9–§11 and §15 fix most of the server's behaviour, but leave some mechanics
to the implementer, and a few runtime needs have no column in the §10.1 schema.
Per SPEC §1 rule 4 we record the choices and the deviations.

## Decision

1. **Composite workspace foreign keys via `RunSQL`.** Django's ORM has no native
   composite foreign key, so each child table declares a `UniqueConstraint`
   `(workspace_id, id)` on its parent and the grants/triggers migration adds the
   DB-level `(workspace_id, parent_id) → parent(workspace_id, id)` constraints
   with `RunSQL`. This satisfies the §17 "composite workspace foreign keys" box:
   a row can never reference a parent in another workspace.

2. **The grant matrix lives in a migration, not only `db-init.sql`.** Default
   privileges in `db-init.sql` do not apply to the dynamically named test
   database, so the §10.3 grants (and the `audit_event` / `audit_chain_head`
   revokes) are applied in `audit/0002_grants_triggers.py`, which runs for every
   environment including tests.

3. **One-append-per-transaction guard uses a savepoint-id marker.** `append_many`
   marks the connection with the current savepoint id; a second call within the
   same savepoint raises, while sibling savepoints (pytest-django nests the test
   in one atomic block) are distinguished correctly.

4. **Two columns and one table beyond §10.1.** §10.1 has nowhere to store the
   24 h rotation grace secret or the first-login password state, so:
   - `api_key.previous_key_hash` and `api_key.previous_key_expires_at` hold the
     prior secret's hash during the rotation grace window (§15.2).
   - `UserPasswordState(user, must_change, temp_expires_at)` records the
     first-login password-change requirement and temporary-password expiry
     (§15.2). Users are global Django users, so this per-user state cannot live
     on a workspace table.
   Both are additive and change no §10.1 column.

5. **The active policy is applied by the worker, not at submit.** On the model
   path, `submit` creates the evaluation in `processing` and enqueues the worker
   on commit; the worker reads the active policy and routes. `submit` itself
   never needs the policy (rule-only and pre-model-gate paths do not consult
   one), so routing under a policy happens in exactly one place.

6. **Fencing guard extracted as `route_under_guard`.** The worker's step-3/4
   "route only if `status = processing` and `model_attempts = token`" logic is a
   named function so the fencing race is tested directly with a stale and a
   current token, rather than through Celery.

7. **Result wait is a per-request Redis subscription (§11.4).** Each waiting
   request subscribes to `adjudik:eval:{id}` and re-reads the status in slices,
   rather than the spec's one shared background thread with a pattern
   subscription per process. The observable behaviour (200 when final, 202 when
   still processing, per-process and per-key waiter caps, no DB connection held
   while waiting) matches; the shared-thread optimisation is deferred.

8. **Reconcile verification is a documented subset for M3a.** `reconcile`
   recomputes `definition_hash` for every published version and `input_hash`
   where input and salt still exist, on top of the full chain and checkpoint
   checks. The per-evaluation decision-fold and policy-threshold reconciliations
   (§9.6) arrive with the retention/purge work in M3b, where the "reconcile
   passes after a purge" test needs them.

9. **Webhook outbox rows are deferred to M3b.** §11.3 step 5 lists inserting
   webhook outbox rows in the evaluate transaction, but webhooks (§11.6) are an
   M3b box; the evaluate and worker transactions create audit events and review
   tasks now and will add outbox rows when the dispatcher lands.

## Consequences

- The schema carries two extra `api_key` columns and one extra table; these are
  noted here so a future schema review does not treat them as drift.
- `reconcile` and the result-wait implementation are intentionally partial and
  will be completed in M3b alongside retention and the shared-waiter thread.
