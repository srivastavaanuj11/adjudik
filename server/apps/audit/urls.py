"""Audit routes (SPEC §11.2)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("audit/events", views.AuditEventsView.as_view()),
    path("audit/checkpoints", views.AuditCheckpointsView.as_view()),
    path("audit/public-key", views.AuditPublicKeyView.as_view()),
    path("audit/export", views.AuditExportView.as_view()),
    path("audit/verifications", views.AuditVerificationsView.as_view()),
    path("audit/verifications/latest", views.AuditVerificationLatestView.as_view()),
    path("audit/verifications/<uuid:verification_id>", views.AuditVerificationDetailView.as_view()),
]
