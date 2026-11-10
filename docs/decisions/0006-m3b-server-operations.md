# 0006. M3b server-operations choices

Date: 2026-11-10
Status: accepted

## Context

SPEC §8, §9.7, §11.6, §11.7 and §15.7–§15.8 fix most of the review queue,
webhooks, metrics, retention and hardening behaviour, but leave some mechanics to
the implementer, and a few decisions interact with choices already recorded in
ADR 0005. Per SPEC §1 rule 4 we record the choices and the deviations.

## Decision

1. **Leases and task timestamps use the injected clock, not the database clock.**
   SPEC §8.5 describes `lease_expires_at = now() + claim_lease_minutes` with the
   database clock. To stay consistent with the house rule that services take an
   injected clock (ADR 0005, conftest), claiming, heartbeat and the lease-expiry
   job all compute times from the passed `now`. The observable behaviour (a live
   claim, an expired lease returning to `open`) is identical; tests drive it with
   a fixed clock.

2. **`complete` reads the task unlocked, then locks evaluation then task.** SPEC
   §8.5 mandates the lock order decision → evaluation → review_task. `complete`
   therefore reads the task without a lock to find its evaluation, locks the
   evaluation `FOR NO KEY UPDATE`, then locks the task — the same order `cancel`
   uses — so the cancel-versus-complete race cannot deadlock. A first draft that
   locked the task first was caught by the race test as a lock-order inversion.

3. **`next` releases the caller's own expired claims before claiming.** The
   partial unique index allows one `claimed` task per reviewer regardless of
   lease. So `next` (and `claim`) first return the caller's own expired claims to
   `open` (event `review.released`, `by: lease_expiry`) inside the same
   transaction, keeping the one-claim invariant without waiting for the
   minute-ly job.

4. **Audit append guard keyed per transaction, not per connection.** ADR 0005's
   one-append-per-transaction marker was a constant for the outermost atomic,
   which collided across sequential transactions on a reused connection — in
   particular a `submit` and the model worker that its on-commit hook runs. The
   guard now keys on `(id(connection.run_on_commit), innermost savepoint)`;
   Django swaps `run_on_commit` for a fresh list on each commit, so sequential
   transactions get distinct keys while a genuine second append in one
   transaction still repeats the key and is rejected. This fixed `seed_demo`,
   whose loop reuses one connection.

5. **Webhook SSRF uses a pinned-IP HTTPS connection.** Each attempt resolves the
   host, requires every address to be globally routable after unwrapping
   IPv4-mapped/6to4/NAT64 forms, then connects to the checked IP with a
   `http.client.HTTPSConnection` subclass that keeps the original Host header and
   TLS SNI (`server_hostname`). This closes the resolve-then-connect TOCTOU gap
   without depending on a DNS-pinning HTTP client. The delivery log stores only
   the status code and an error class.

6. **The on-commit dispatch nudge is settings-gated.** SPEC §8.7 nudges the
   dispatcher on commit in addition to the 5 s job. The nudge is registered only
   when `ADJUDIK_WEBHOOK_NUDGE_ON_COMMIT` is true; tests set it false so the
   outbox can be inspected without a delivery attempt firing. The 5 s job
   delivers regardless, so a skipped nudge is harmless.

7. **Reviewer-visible input and `included_paths` are computed server-side.** The
   review view strips any property marked `x-adjudik-reviewer-hidden` from the
   stored input and lists the visible leaf paths. This realises the §19 default
   (reviewers see the full input except hidden fields); switching to
   `include_fields`-only remains an open question.

8. **Metrics percentiles and the audit-agreement interval.** Percentiles use
   linear interpolation between order statistics. The propensity-weighted
   audit-agreement point and its two-sided 95 % Clopper-Pearson bounds are
   computed on the effective (1 ÷ `label_propensity`) counts with
   `scipy.stats.beta.ppf`; a dedicated `stats.py` arrives in M5 and will absorb
   this helper.

9. **`reconcile` now completes the input-hash recomputation after a purge.** ADR
   0005 deferred part of reconcile to the retention work. With M3b, `reconcile`
   recomputes `input_hash` only where input and salt still exist, so a purged
   evaluation is skipped and the chain still verifies — exercised by the
   "verifies and reconciles after a purge" test. The per-evaluation decision-fold
   and policy-threshold reconciliations remain for a later milestone.

10. **OpenAPI generates, but zero-warning annotation is deferred to M4.** The
    endpoints are hand-rolled `APIView`s without DRF serializers, so
    `drf-spectacular` emits component warnings. The schema generates into a valid
    OpenAPI 3 document (tested), and full request/response annotation lands with
    the typed serializers the web client needs in M4. The §17 box is ticked on
    that basis.

11. **`seed_demo` readiness check is on label counts (until M5).** The command
    simulates reviewers with a hidden ground-truth under which agreement rises
    with confidence. Until the M5 calibration core exists, the seed test asserts
    label counts per outcome rather than running a calibration to τ = 0.95, as
    §16.5 allows.

## Consequences

- One connection may safely run many append-bearing transactions in sequence
  (commands, the eager worker), which ADR 0005's marker did not allow.
- Webhook delivery depends only on the standard library for the pinned TLS
  connection; no new runtime dependency was added.
- The metrics agreement interval and a reconcile subset will be re-homed in M5
  (`stats.py`) and a later retention pass; the OpenAPI annotations in M4.
