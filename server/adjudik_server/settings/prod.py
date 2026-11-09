"""Production settings (SPEC §15.7).

M0 sets the baseline security posture. The start-up refusal checks for each
§15.7 condition (DEBUG off, real secret key, HTTPS/HSTS, signing key present,
no insecure/private webhooks, etc.) are added and individually tested in M3b.
"""

from __future__ import annotations

from .base import *  # noqa: F401,F403

DEBUG = False

# Secure cookies and transport. HSTS tuned in M3b.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_HTTPONLY = True
