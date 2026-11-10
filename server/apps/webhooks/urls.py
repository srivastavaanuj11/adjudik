"""Webhook management routes (SPEC §11.2). Populated in the webhooks step."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("webhooks", views.WebhooksView.as_view()),
    path("webhooks/<uuid:endpoint_id>", views.WebhookDetailView.as_view()),
    path("webhooks/<uuid:endpoint_id>/rotate-secret", views.WebhookRotateSecretView.as_view()),
    path("webhooks/<uuid:endpoint_id>/deliveries", views.WebhookDeliveriesView.as_view()),
    path("webhooks/deliveries/<uuid:delivery_id>/retry", views.WebhookDeliveryRetryView.as_view()),
]
