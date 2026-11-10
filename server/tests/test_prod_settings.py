"""Production start-up refusal, one test per §15.7 condition (SPEC §15.7, §17 M3b)."""

from __future__ import annotations

import pytest

from adjudik_server.settings.hardening import production_failures

# A fully valid production configuration; each test flips one field bad.
GOOD = {
    "debug": False,
    "secret_key": "a-real-long-production-secret-key",
    "encryption_keys": "ZmVybmV0LWtleQ==",
    "signing_key": "ed25519-key",
    "allow_private_webhooks": False,
    "allow_insecure_webhooks": False,
    "allowed_hosts": ["adjudik.example.com"],
    "db_user": "adjudik_app",
    "db_password": "a-strong-db-password",
    "redis_url": "redis://:redis-pw@redis:6379/0",
}


def test_good_config_passes():
    assert production_failures(**GOOD) == []


@pytest.mark.parametrize(
    "override",
    [
        {"debug": True},
        {"secret_key": ""},
        {"secret_key": "dev-insecure-secret-key-change-me"},
        {"encryption_keys": ""},
        {"signing_key": ""},
        {"allow_private_webhooks": True},
        {"allow_insecure_webhooks": True},
        {"allowed_hosts": ["adjudik.example.com", "*"]},
        {"db_password": "adjudik"},
        {"db_user": "adjudik_owner"},
        {"redis_url": "redis://redis:6379/0"},  # no password
    ],
    ids=[
        "debug",
        "missing_secret_key",
        "default_secret_key",
        "missing_encryption_keys",
        "missing_signing_key",
        "allow_private_webhooks",
        "allow_insecure_webhooks",
        "wildcard_allowed_hosts",
        "default_db_password",
        "owner_db_role",
        "passwordless_redis",
    ],
)
def test_each_condition_refuses(override):
    cfg = {**GOOD, **override}
    failures = production_failures(**cfg)
    assert len(failures) == 1, failures  # exactly the one bad condition
