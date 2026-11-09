"""Development settings."""

from __future__ import annotations

from .base import *  # noqa: F401,F403
from .base import env_bool

DEBUG = env_bool("DJANGO_DEBUG", True)
# Dev convenience: allow all common local hosts plus the compose service names.
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "0.0.0.0", "server", "web"]
