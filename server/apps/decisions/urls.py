"""Decision, version and policy routes (SPEC §11.2)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("decisions", views.DecisionsView.as_view()),
    path("decisions/validate", views.ValidateView.as_view()),
    path("decisions/<str:key>/legal-hold", views.DecisionLegalHoldView.as_view()),
    path("decisions/<str:key>", views.DecisionDetailView.as_view()),
    path("decisions/<str:key>/versions", views.VersionsView.as_view()),
    path("decisions/<str:key>/versions/<int:n>", views.VersionDetailView.as_view()),
    path("decisions/<str:key>/versions/<int:n>/publish", views.PublishView.as_view()),
    path("decisions/<str:key>/versions/<int:n>/retire", views.RetireView.as_view()),
    path(
        "decisions/<str:key>/versions/<int:n>/acknowledge-model",
        views.AcknowledgeModelView.as_view(),
    ),
    path("decisions/<str:key>/versions/<int:n>/dry-run", views.DryRunView.as_view()),
    path("decisions/<str:key>/versions/<int:n>/render-prompt", views.RenderPromptView.as_view()),
    path("decisions/<str:key>/versions/<int:n>/policies", views.VersionPoliciesView.as_view()),
    path("decisions/<str:key>/versions/<int:n>/policy", views.VersionActivePolicyView.as_view()),
    path("policies/<uuid:policy_id>/approve", views.PolicyApproveView.as_view()),
    path("policies/<uuid:policy_id>/reject", views.PolicyRejectView.as_view()),
    path("policies/<uuid:policy_id>/deactivate", views.PolicyDeactivateView.as_view()),
]
