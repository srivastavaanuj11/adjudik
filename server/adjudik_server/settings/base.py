"""Shared Django settings for Adjudik (SPEC §4, §10.3, §11).

Environment-specific modules (``dev``, ``prod``, ``test``) import ``*`` from here
and override. M0 keeps this to what the scaffold and health endpoints need;
auth, DRF auth classes, throttles, Celery queues and the audit/crypto config are
extended in later milestones.
"""

from __future__ import annotations

import os
from pathlib import Path

import dj_database_url

# server/ directory (contains manage.py).
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key: str, default: str | None = None) -> str | None:
    return os.environ.get(key, default)


def env_bool(key: str, default: bool = False) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = env_bool("DJANGO_DEBUG", False)
_allowed = env("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1") or ""
ALLOWED_HOSTS = [h.strip() for h in _allowed.split(",") if h.strip()]

# --- Applications ---
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "drf_spectacular",
    "django_filters",
]
# Adjudik apps (SPEC §4.4). Registered as they gain models in later milestones.
LOCAL_APPS: list[str] = [
    "apps.accounts",
    "apps.decisions",
    "apps.evaluations",
    "apps.reviews",
    "apps.audit",
    "apps.webhooks",
    "apps.metrics",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "adjudik_server.urls"
WSGI_APPLICATION = "adjudik_server.wsgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

# --- Database (SPEC §10.3, §1.1) ---
# Runtime uses DATABASE_URL (adjudik_app); migrate/tests swap in DATABASE_OWNER_URL.
_DATABASE_URL = env("DATABASE_URL", "postgres://adjudik_app:adjudik_app_pw@localhost:5432/adjudik")
DATABASES = {
    "default": {
        **dj_database_url.parse(_DATABASE_URL, conn_max_age=0),
        "OPTIONS": {"pool": {"max_size": 20}},
    }
}
# SQLite is not supported (SPEC §4.3); fail loudly if someone points us at it.
if "sqlite" in DATABASES["default"].get("ENGINE", ""):
    raise RuntimeError("Adjudik requires PostgreSQL; SQLite is not supported (SPEC §4.3).")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Cache / Redis ---
REDIS_URL = env("REDIS_URL", "redis://localhost:6379/0")

# --- Celery (SPEC §4.1) ---
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = None
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# --- DRF (SPEC §11.1) ---
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Adjudik API",
    "DESCRIPTION": "Adjudik decision engine REST API (SPEC §11).",
    "VERSION": "1",
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "SERVE_INCLUDE_SCHEMA": False,
}

# --- i18n / tz ---
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# --- Static files (SPA served via WhiteNoise in prod, SPEC §16.2) ---
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# --- Adjudik config (SPEC §16.3) ---
ADJUDIK_ENCRYPTION_KEYS = env("ADJUDIK_ENCRYPTION_KEYS", "")
ADJUDIK_AUDIT_SIGNING_KEY = env("ADJUDIK_AUDIT_SIGNING_KEY", "")
ADJUDIK_AUDIT_ANCHOR_URL = env("ADJUDIK_AUDIT_ANCHOR_URL", "")
ADJUDIK_PROVIDERS = env("ADJUDIK_PROVIDERS", "")
ADJUDIK_DEFAULT_WAIT_MS = int(env("ADJUDIK_DEFAULT_WAIT_MS", "10000"))
ADJUDIK_MAX_WAITERS_PER_PROCESS = int(env("ADJUDIK_MAX_WAITERS_PER_PROCESS", "64"))
ADJUDIK_STORE_PROMPTS = env_bool("ADJUDIK_STORE_PROMPTS", True)
ADJUDIK_ALLOW_PRIVATE_WEBHOOKS = env_bool("ADJUDIK_ALLOW_PRIVATE_WEBHOOKS", False)
ADJUDIK_ALLOW_INSECURE_WEBHOOKS = env_bool("ADJUDIK_ALLOW_INSECURE_WEBHOOKS", False)

# --- Logging (structlog wiring extended in M3b) ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
