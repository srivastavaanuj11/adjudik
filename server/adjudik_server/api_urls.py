"""The ``/api/v1`` URL tree (SPEC §11.2).

Each app contributes its routes; they are added here as the milestones build
them (accounts in M3a first, then decisions, evaluations, reviews, audit).
"""

from __future__ import annotations

from django.urls import include, path

urlpatterns = [
    path("", include("apps.accounts.urls")),
    path("", include("apps.decisions.urls")),
    path("", include("apps.evaluations.urls")),
    path("", include("apps.reviews.urls")),
    path("", include("apps.webhooks.urls")),
    path("", include("apps.metrics.urls")),
    path("", include("apps.audit.urls")),
]
