"""Account-level routes (SPEC §11.2)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf", views.CsrfView.as_view()),
    path("auth/login", views.LoginView.as_view()),
    path("auth/logout", views.LogoutView.as_view()),
    path("auth/me", views.MeView.as_view()),
    path("auth/workspace", views.WorkspaceSwitchView.as_view()),
    path("auth/password", views.PasswordChangeView.as_view()),
    path("workspace/settings", views.WorkspaceSettingsView.as_view()),
    path("members", views.MembersView.as_view()),
    path("members/<uuid:member_id>", views.MemberDetailView.as_view()),
    path("api-keys", views.ApiKeysView.as_view()),
    path("api-keys/<uuid:key_id>/revoke", views.ApiKeyRevokeView.as_view()),
    path("api-keys/<uuid:key_id>/rotate", views.ApiKeyRotateView.as_view()),
]
