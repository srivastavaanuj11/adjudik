"""Serve the built single-page app (SPEC §16.2).

In production the React build is collected into ``STATIC_ROOT`` and its assets are
served by WhiteNoise under ``/static/``. Any path outside ``/api``, ``/healthz``,
``/readyz`` and ``/static`` returns ``index.html`` so SPA deep links work on a
full-page load. When no build is present (development, where Vite serves the SPA)
the catch-all returns 404 and is effectively inert.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, HttpResponseNotFound


def _candidates() -> list[Path]:
    roots: list[Path] = []
    if settings.STATIC_ROOT:
        roots.append(Path(settings.STATIC_ROOT) / "index.html")
    # Fallback to the raw Vite build next to the repo (useful before collectstatic).
    roots.append(Path(settings.BASE_DIR).parent / "web" / "dist" / "index.html")
    return roots


@lru_cache(maxsize=1)
def _index_html() -> str | None:
    for path in _candidates():
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def spa_index(request):  # noqa: ARG001 - Django view signature
    html = _index_html()
    if html is None:
        return HttpResponseNotFound("SPA build not found")
    return HttpResponse(html, content_type="text/html; charset=utf-8")
