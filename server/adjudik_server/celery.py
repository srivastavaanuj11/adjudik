"""Celery application for Adjudik (SPEC §4.1).

One worker per queue: ``model``, ``webhooks``, ``jobs``, ``default``. Tasks are
registered by the apps in later milestones; M0 only wires the app up.
"""

from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "adjudik_server.settings.dev")

app = Celery("adjudik")
# All Celery config lives under the CELERY_ namespace in Django settings.
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=True)
def debug_task(self) -> None:  # pragma: no cover - diagnostic only
    print(f"Request: {self.request!r}")
