"""Production settings (SPEC §15.7).

Beyond the baseline security posture, production refuses to start — naming the
failed check — for each §15.7 condition (see :mod:`hardening`). It also sets the
CSP, denies framing, and never installs ``django.contrib.admin``. The container
runs as a non-root user (enforced in the image, SPEC §15.7).
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured

from .base import *  # noqa: F401,F403
from .base import (  # noqa: F401
    ADJUDIK_ALLOW_INSECURE_WEBHOOKS,
    ADJUDIK_ALLOW_PRIVATE_WEBHOOKS,
    ADJUDIK_AUDIT_SIGNING_KEY,
    ADJUDIK_ENCRYPTION_KEYS,
    ALLOWED_HOSTS,
    DATABASES,
    REDIS_URL,
    SECRET_KEY,
)
from .hardening import production_failures

DEBUG = False

# Secure cookies and transport (SPEC §15.7).
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # one year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
X_FRAME_OPTIONS = "DENY"

# Content Security Policy (SPEC §15.7), emitted by the common middleware.
ADJUDIK_CSP = "default-src 'self'; object-src 'none'; frame-ancestors 'none'"

# django.contrib.admin is never installed (SPEC §15.7); base does not add it.


def _enforce_production_settings() -> None:
    db = DATABASES["default"]
    failures = production_failures(
        debug=DEBUG,
        secret_key=SECRET_KEY,
        encryption_keys=ADJUDIK_ENCRYPTION_KEYS,
        signing_key=ADJUDIK_AUDIT_SIGNING_KEY,
        allow_private_webhooks=ADJUDIK_ALLOW_PRIVATE_WEBHOOKS,
        allow_insecure_webhooks=ADJUDIK_ALLOW_INSECURE_WEBHOOKS,
        allowed_hosts=ALLOWED_HOSTS,
        db_user=db.get("USER"),
        db_password=db.get("PASSWORD"),
        redis_url=REDIS_URL,
    )
    if failures:
        raise ImproperlyConfigured(
            "Production start-up refused (SPEC §15.7): " + "; ".join(failures)
        )


_enforce_production_settings()
