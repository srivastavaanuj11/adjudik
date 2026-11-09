"""Gunicorn configuration for production (SPEC §11.4, §16.2).

Sync workers behind the reverse proxy; the evaluate-and-wait path holds a request
open for up to ``wait_ms``, so the timeout is generous. Tuned further in M4.
"""

from __future__ import annotations

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", str(multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
worker_class = "gthread"
timeout = int(os.environ.get("GUNICORN_TIMEOUT", "60"))
graceful_timeout = 30
keepalive = 5
accesslog = "-"
errorlog = "-"
