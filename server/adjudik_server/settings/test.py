"""Test settings (SPEC §16.4).

Tests run against real Postgres and Redis, connecting as the owner role
(``DATABASE_OWNER_URL``); immutability tests open a second connection as
``adjudik_app``. Celery runs eagerly except in the marked integration tests.
"""

from __future__ import annotations

import os

from .base import *  # noqa: F401,F403
from .base import DATABASES, env

DEBUG = False

# Owner role runs the test DB (creates/drops the schema).
_owner_url = env("DATABASE_OWNER_URL")
if _owner_url:
    import dj_database_url

    DATABASES["default"] = {
        **dj_database_url.parse(_owner_url, conn_max_age=0),
        "OPTIONS": {"pool": {"max_size": 20}},
    }

# Celery eager by default in tests; integration tests override.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Never fire a webhook delivery from an on-commit nudge during unit tests; the
# dispatcher is driven directly where a test wants it (SPEC §8.7).
ADJUDIK_WEBHOOK_NUDGE_ON_COMMIT = False

# Keep password hashing fast in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

os.environ.setdefault("ADJUDIK_PROVIDERS", "")

# A deterministic-per-run Ed25519 audit signing key, unless one is provided.
ADJUDIK_AUDIT_SIGNING_KEY = env("ADJUDIK_AUDIT_SIGNING_KEY", "")
if not ADJUDIK_AUDIT_SIGNING_KEY:
    from adjudik_core import audit_chain as _audit_chain

    ADJUDIK_AUDIT_SIGNING_KEY, _ = _audit_chain.generate_signing_key()
