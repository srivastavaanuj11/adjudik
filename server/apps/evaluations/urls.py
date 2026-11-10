"""Evaluate and evaluation-read routes (SPEC §11.2)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("decisions/<str:key>/evaluate", views.EvaluateView.as_view()),
    path("evaluations", views.EvaluationsView.as_view()),
    path("evaluations/<uuid:eval_id>", views.EvaluationDetailView.as_view()),
]
