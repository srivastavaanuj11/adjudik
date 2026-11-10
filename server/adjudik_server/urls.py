"""Root URL configuration (SPEC §11).

M0: health endpoints at the root and the OpenAPI schema. The ``/api/v1`` router
and the SPA catch-all are added in M3a and M4 respectively.
"""

from __future__ import annotations

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from .health import healthz, readyz

urlpatterns = [
    path("healthz", healthz, name="healthz"),
    path("readyz", readyz, name="readyz"),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/", include("adjudik_server.api_urls")),
]
