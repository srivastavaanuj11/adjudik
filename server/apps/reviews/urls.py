"""Review queue routes (SPEC §11.2)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("reviews", views.ReviewsView.as_view()),
    path("reviews/next", views.ReviewNextView.as_view()),
    path("reviews/<uuid:task_id>", views.ReviewDetailView.as_view()),
    path("reviews/<uuid:task_id>/claim", views.ReviewClaimView.as_view()),
    path("reviews/<uuid:task_id>/heartbeat", views.ReviewHeartbeatView.as_view()),
    path("reviews/<uuid:task_id>/release", views.ReviewReleaseView.as_view()),
    path("reviews/<uuid:task_id>/complete", views.ReviewCompleteView.as_view()),
]
