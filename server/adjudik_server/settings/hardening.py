"""Production start-up refusal checks (SPEC §15.7).

``production_failures`` returns the names of every failed check; ``prod.py`` calls
it with the live settings and refuses to start if the list is non-empty. Keeping
the logic pure makes each §15.7 condition testable in isolation.
"""

from __future__ import annotations

from urllib.parse import urlsplit

DEFAULT_SECRET_KEY = "dev-insecure-secret-key-change-me"
OWNER_ROLE = "adjudik_owner"
FORBIDDEN_DB_PASSWORD = "adjudik"


def production_failures(
    *,
    debug: bool,
    secret_key: str | None,
    encryption_keys: str | None,
    signing_key: str | None,
    allow_private_webhooks: bool,
    allow_insecure_webhooks: bool,
    allowed_hosts: list[str],
    db_user: str | None,
    db_password: str | None,
    redis_url: str | None,
) -> list[str]:
    """Return the failed §15.7 checks (empty means production may start)."""
    failures: list[str] = []
    if debug:
        failures.append("DEBUG must be false in production")
    if not secret_key or secret_key == DEFAULT_SECRET_KEY:
        failures.append("DJANGO_SECRET_KEY must be set to a non-default value")
    if not encryption_keys:
        failures.append("ADJUDIK_ENCRYPTION_KEYS must be set")
    if not signing_key:
        failures.append("ADJUDIK_AUDIT_SIGNING_KEY must be set")
    if allow_private_webhooks:
        failures.append("ADJUDIK_ALLOW_PRIVATE_WEBHOOKS must not be set in production")
    if allow_insecure_webhooks:
        failures.append("ADJUDIK_ALLOW_INSECURE_WEBHOOKS must not be set in production")
    if "*" in (allowed_hosts or []):
        failures.append("ALLOWED_HOSTS must not contain '*'")
    if db_password == FORBIDDEN_DB_PASSWORD:
        failures.append("the database password must not be the default 'adjudik'")
    if (db_user or "") == OWNER_ROLE:
        failures.append("DATABASE_URL must not connect as the owner role")
    if not _redis_has_password(redis_url):
        failures.append("REDIS_URL must include a password")
    return failures


def _redis_has_password(redis_url: str | None) -> bool:
    if not redis_url:
        return False
    return bool(urlsplit(redis_url).password)
