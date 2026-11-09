"""Adjudik Django project.

Ensure the Celery app is imported when Django starts so that the ``@shared_task``
decorator uses it (standard Celery + Django wiring).
"""

from __future__ import annotations

from .celery import app as celery_app

__all__ = ["celery_app"]
