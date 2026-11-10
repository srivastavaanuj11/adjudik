"""Periodic jobs and their advisory-lock skip (SPEC §8.7, §17 M3b).

Each job holds a distinct ``pg_try_advisory_lock`` key and returns ``None`` when
another instance already holds it. The test takes the lock on a *second*
connection, then calls the task wrapper on Django's connection and asserts it
skipped.
"""

from __future__ import annotations

import pytest

from apps.audit import tasks as audit_tasks
from apps.evaluations import tasks as eval_tasks
from apps.reviews import tasks as review_tasks
from apps.webhooks import tasks as webhook_tasks

# (task callable, advisory-lock key) for every periodic job.
JOBS = [
    (review_tasks.release_expired_leases_task, review_tasks._LEASE_LOCK),
    (eval_tasks.sweep_stuck_evaluations_task, eval_tasks.ADVISORY_SWEEP_KEY),
    (eval_tasks.purge_expired_data_task, eval_tasks.ADVISORY_PURGE_KEY),
    (webhook_tasks.dispatch_webhooks_task, webhook_tasks._DISPATCH_LOCK),
    (webhook_tasks.prune_webhook_deliveries_task, webhook_tasks._PRUNE_LOCK),
    (audit_tasks.write_audit_checkpoints, audit_tasks._CHECKPOINT_LOCK),
    (audit_tasks.verify_audit_chains, audit_tasks._VERIFY_LOCK),
]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("task, lock_key", JOBS, ids=[t.__name__ for t, _ in JOBS])
def test_job_skips_when_lock_held(task, lock_key, app_dsn):
    import psycopg

    holder = psycopg.connect(app_dsn)
    try:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", [lock_key])
            assert cur.fetchone()[0] is True
        holder.commit()
        # Another instance already holds the lock, so the run is skipped.
        assert task.run() is None
    finally:
        with holder.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", [lock_key])
        holder.commit()
        holder.close()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("task", [t for t, _ in JOBS], ids=[t.__name__ for t, _ in JOBS])
def test_job_runs_when_lock_free(task):
    # With no contending lock, the wrapper runs and returns a non-None result.
    assert task.run() is not None
