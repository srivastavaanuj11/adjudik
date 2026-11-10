"""Metrics route (SPEC §11.2, §11.7)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("decisions/<str:key>/metrics", views.MetricsView.as_view()),
]
