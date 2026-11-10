"""Account-level endpoints: auth, members, API keys, workspace settings (SPEC §11.2)."""

from __future__ import annotations

import datetime as _dt
import hashlib

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.password_validation import validate_password
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.middleware.csrf import get_token
from django.utils import timezone
from rest_framework.response import Response

from apps.audit.models import AuditChainHead
from apps.common import audit, permissions
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.errors import (
    ApiError,
    bad_request,
    conflict,
    not_found,
    unauthenticated,
    unprocessable,
)
from apps.common.views import AdjudikAPIView
from apps.decisions.models import Decision

from . import service
from .models import (
    ApiKey,
    ApiKeyScope,
    Membership,
    Role,
    UserPasswordState,
    WorkspaceSettings,
)

SYSTEM_WORKSPACE_ID = service.SYSTEM_WORKSPACE_ID


def _client_ip(request) -> str:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "0.0.0.0")


def _username_hash(username: str) -> str:
    return hashlib.sha256(username.encode("utf-8")).hexdigest()


def _append_system(events: list[Event]) -> None:
    AuditChainHead.objects.get_or_create(
        workspace_id=SYSTEM_WORKSPACE_ID, defaults={"seq": 0, "hash": "0" * 64}
    )
    audit.append_many(SYSTEM_WORKSPACE_ID, events)


# --- Auth ---


class CsrfView(AdjudikAPIView):
    login_optional = True

    def get(self, request):
        return Response({"csrfToken": get_token(request)})


class LoginView(AdjudikAPIView):
    login_optional = True

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        username = data.get("username")
        password = data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise bad_request("username and password are required")

        now = timezone.now()
        self._check_lockout(username, _client_ip(request))

        user = authenticate(request, username=username, password=password)
        if user is None:
            self._record_failure(username, _client_ip(request), now)
            with transaction.atomic():
                _append_system(
                    [
                        Event(
                            "auth.login_failed",
                            now,
                            actor_type="system",
                            payload={"username_hash": _username_hash(username)},
                        )
                    ]
                )
            raise unauthenticated("invalid credentials")

        self._clear_failures(username, _client_ip(request))

        state = UserPasswordState.objects.filter(user=user).first()
        if state and state.must_change:
            expired = state.temp_expires_at is not None and state.temp_expires_at <= now
            # Allow the session so the user can change the password, but signal it.
            login(request, user)
            with transaction.atomic():
                _append_system(
                    [
                        Event(
                            "auth.login",
                            now,
                            actor_type="user",
                            actor_id=str(user.id),
                            payload={"user_id": str(user.id)},
                        )
                    ]
                )
            raise ApiError(
                "password_change_required",
                "temporary password expired" if expired else "password change required",
                http_status=401,
            )

        login(request, user)
        with transaction.atomic():
            _append_system(
                [
                    Event(
                        "auth.login",
                        now,
                        actor_type="user",
                        actor_id=str(user.id),
                        payload={"user_id": str(user.id)},
                    )
                ]
            )
        return Response(self._me(request, user))

    def _check_lockout(self, username: str, ip: str) -> None:
        for key in (f"lockout:u:{username}", f"lockout:ip:{ip}"):
            until = cache.get(key)
            if until:
                raise ApiError("rate_limited", "too many attempts", http_status=429)

    def _record_failure(self, username: str, ip: str, now: _dt.datetime) -> None:
        from django.conf import settings

        max_attempts = settings.ADJUDIK_LOGIN_MAX_ATTEMPTS
        for scope, ident in (("u", username), ("ip", ip)):
            ckey = f"login_fail:{scope}:{ident}"
            count = cache.get(ckey, 0) + 1
            cache.set(ckey, count, timeout=60)
            if count >= max_attempts:
                lkey = f"lockout:{scope}:{ident}"
                prior = cache.get(f"lockout_min:{scope}:{ident}", 0)
                minutes = min(60, max(1, prior * 2 if prior else 1))
                cache.set(lkey, True, timeout=minutes * 60)
                cache.set(f"lockout_min:{scope}:{ident}", minutes, timeout=60 * 60)
                cache.delete(ckey)

    def _clear_failures(self, username: str, ip: str) -> None:
        for scope, ident in (("u", username), ("ip", ip)):
            cache.delete(f"login_fail:{scope}:{ident}")

    def _me(self, request, user) -> dict:
        return MeView.build(request, user)


class LogoutView(AdjudikAPIView):
    def post(self, request):
        user = request.user
        now = timezone.now()
        if getattr(user, "is_authenticated", False):
            with transaction.atomic():
                _append_system(
                    [
                        Event(
                            "auth.logout",
                            now,
                            actor_type="user",
                            actor_id=str(user.id),
                            payload={"user_id": str(user.id)},
                        )
                    ]
                )
        logout(request)
        return Response({"status": "ok"})


class MeView(AdjudikAPIView):
    def get(self, request):
        return Response(self.build(request, request.user))

    @staticmethod
    def build(request, user) -> dict:
        memberships = list(
            Membership.objects.select_related("workspace").filter(user=user).order_by("created_at")
        )
        ctx = getattr(request, "adjudik", None)
        active = ctx.workspace if ctx else (memberships[0].workspace if memberships else None)
        active_role = None
        if active:
            for m in memberships:
                if m.workspace_id == active.id:
                    active_role = m.role
                    break
        return {
            "username": user.get_username(),
            "active_workspace": {"id": str(active.id), "slug": active.slug, "name": active.name}
            if active
            else None,
            "role": active_role,
            "memberships": [
                {"workspace": {"id": str(m.workspace_id), "slug": m.workspace.slug}, "role": m.role}
                for m in memberships
            ],
        }


class WorkspaceSwitchView(AdjudikAPIView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        slug = data.get("workspace")
        m = (
            Membership.objects.select_related("workspace")
            .filter(user=request.user, workspace__slug=slug)
            .first()
        )
        if m is None:
            raise not_found("workspace")
        request.session["active_workspace_id"] = str(m.workspace_id)
        return Response({"active_workspace": {"id": str(m.workspace_id), "slug": m.workspace.slug}})


class PasswordChangeView(AdjudikAPIView):
    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        new_password = data.get("new_password")
        if not isinstance(new_password, str):
            raise bad_request("new_password is required")
        try:
            validate_password(new_password, user=request.user)
        except ValidationError as exc:
            raise unprocessable(
                "input_invalid",
                "password does not meet requirements",
                details=[{"code": "password_invalid", "message": m} for m in exc.messages],
            ) from exc
        now = timezone.now()
        with transaction.atomic():
            user = request.user
            user.set_password(new_password)
            user.save(update_fields=["password"])
            UserPasswordState.objects.update_or_create(
                user=user, defaults={"must_change": False, "temp_expires_at": None}
            )
            _append_system(
                [
                    Event(
                        "auth.password_changed",
                        now,
                        actor_type="user",
                        actor_id=str(user.id),
                        payload={"user_id": str(user.id)},
                    )
                ]
            )
        # Changing the password flushes other sessions; keep this one logged in.
        from django.contrib.auth import update_session_auth_hash

        update_session_auth_hash(request, request.user)
        return Response({"status": "ok"})


# --- Members (admin) ---


class MembersView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "admin")
        members = Membership.objects.select_related("user").filter(workspace=self.ctx.workspace)
        return Response(
            {
                "results": [
                    {"id": str(m.id), "username": m.user.get_username(), "role": m.role}
                    for m in members
                ]
            }
        )

    def post(self, request):
        permissions.require(self.ctx, "admin")
        data = request.data if isinstance(request.data, dict) else {}
        username = data.get("username")
        role = data.get("role")
        if not isinstance(username, str) or role not in Role.values:
            raise unprocessable("input_invalid", "username and a valid role are required")

        now = timezone.now()
        model = get_user_model()
        temp_password = None
        with transaction.atomic():
            user = model.objects.filter(username=username).first()
            if user is None:
                user, temp_password = service.create_user_with_temp_password(username, now=now)
            if Membership.objects.filter(workspace=self.ctx.workspace, user=user).exists():
                raise conflict("invalid_state", "user is already a member")
            m = service.add_member(self.ctx.workspace, user, role, now=now)
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "membership.changed",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="user",
                        subject_id=str(user.id),
                        payload={"user_id": str(user.id), "role": role},
                    )
                ],
            )
        body = {"id": str(m.id), "username": username, "role": role}
        if temp_password is not None:
            body["temporary_password"] = temp_password  # returned once
        return Response(body, status=201)


class MemberDetailView(AdjudikAPIView):
    def patch(self, request, member_id):
        permissions.require(self.ctx, "admin")
        m = Membership.objects.filter(workspace=self.ctx.workspace, id=member_id).first()
        if m is None:
            raise not_found("member")
        data = request.data if isinstance(request.data, dict) else {}
        role = data.get("role")
        if role not in Role.values:
            raise unprocessable("input_invalid", "a valid role is required")
        now = timezone.now()
        with transaction.atomic():
            m.role = role
            m.save(update_fields=["role"])
            _flush_user_sessions(m.user_id)
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "membership.changed",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="user",
                        subject_id=str(m.user_id),
                        payload={"user_id": str(m.user_id), "role": role},
                    )
                ],
            )
        return Response({"id": str(m.id), "role": role})

    def delete(self, request, member_id):
        permissions.require(self.ctx, "admin")
        m = Membership.objects.filter(workspace=self.ctx.workspace, id=member_id).first()
        if m is None:
            raise not_found("member")
        now = timezone.now()
        user_id = m.user_id
        with transaction.atomic():
            m.delete()
            _flush_user_sessions(user_id)
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "membership.changed",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="user",
                        subject_id=str(user_id),
                        payload={"user_id": str(user_id), "role": None},
                    )
                ],
            )
        return Response(status=204)


def _flush_user_sessions(user_id) -> None:
    """Flush a user's sessions after a role change or removal (SPEC §15.2)."""
    from django.contrib.sessions.models import Session

    for s in Session.objects.all():
        data = s.get_decoded()
        if str(data.get("_auth_user_id")) == str(user_id):
            s.delete()


# --- API keys (admin) ---


class ApiKeysView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "admin")
        keys = ApiKey.objects.filter(workspace=self.ctx.workspace).order_by("-created_at")
        return Response({"results": [_api_key_dict(k) for k in keys]})

    def post(self, request):
        permissions.require(self.ctx, "admin")
        data = request.data if isinstance(request.data, dict) else {}
        name = data.get("name")
        scopes = data.get("scopes", [])
        decision_ids = data.get("decision_ids")
        if not isinstance(name, str) or not name:
            raise unprocessable("input_invalid", "name is required")
        if not isinstance(scopes, list) or any(s not in ApiKeyScope.values for s in scopes):
            raise unprocessable("input_invalid", "invalid scopes")
        resolved_ids = None
        if decision_ids is not None:
            if not isinstance(decision_ids, list):
                raise unprocessable("input_invalid", "decision_ids must be a list or null")
            found = Decision.objects.filter(
                workspace=self.ctx.workspace, id__in=decision_ids
            ).values_list("id", flat=True)
            if len(set(map(str, found))) != len(set(map(str, decision_ids))):
                raise unprocessable("input_invalid", "unknown decision id")
            resolved_ids = [str(d) for d in decision_ids]

        now = timezone.now()
        with transaction.atomic():
            issued = service.issue_api_key(
                self.ctx.workspace,
                name=name,
                scopes=scopes,
                decision_ids=resolved_ids,
                now=now,
                created_by=self.ctx.username,
            )
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "api_key.created",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="api_key",
                        subject_id=str(issued.api_key.id),
                        payload={
                            "api_key_id": str(issued.api_key.id),
                            "prefix": issued.api_key.prefix,
                            "scopes": scopes,
                            "decision_ids": resolved_ids,
                            "expires_at": iso_micros(issued.api_key.expires_at),
                        },
                    )
                ],
            )
        body = _api_key_dict(issued.api_key)
        body["key"] = issued.full_key  # returned once
        return Response(body, status=201)


class ApiKeyRevokeView(AdjudikAPIView):
    def post(self, request, key_id):
        permissions.require(self.ctx, "admin")
        key = ApiKey.objects.filter(workspace=self.ctx.workspace, id=key_id).first()
        if key is None:
            raise not_found("api_key")
        now = timezone.now()
        with transaction.atomic():
            if key.revoked_at is None:
                key.revoked_at = now
                key.save(update_fields=["revoked_at"])
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "api_key.revoked",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="api_key",
                        subject_id=str(key.id),
                        payload={"api_key_id": str(key.id), "prefix": key.prefix},
                    )
                ],
            )
        return Response(_api_key_dict(key))


class ApiKeyRotateView(AdjudikAPIView):
    def post(self, request, key_id):
        permissions.require(self.ctx, "admin")
        key = ApiKey.objects.filter(workspace=self.ctx.workspace, id=key_id).first()
        if key is None:
            raise not_found("api_key")
        if key.revoked_at is not None:
            raise conflict("invalid_state", "cannot rotate a revoked key")
        now = timezone.now()
        with transaction.atomic():
            issued = service.rotate_api_key(key, now=now)
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "api_key.rotated",
                        now,
                        actor_type="user",
                        actor_id=self.ctx.actor_id,
                        subject_type="api_key",
                        subject_id=str(key.id),
                        payload={"api_key_id": str(key.id), "prefix": key.prefix},
                    )
                ],
            )
        body = _api_key_dict(issued.api_key)
        body["key"] = issued.full_key
        return Response(body)


def _api_key_dict(k: ApiKey) -> dict:
    return {
        "id": str(k.id),
        "name": k.name,
        "prefix": k.prefix,
        "scopes": k.scopes,
        "decision_ids": k.decision_ids,
        "expires_at": iso_micros(k.expires_at),
        "created_at": iso_micros(k.created_at),
        "last_used_at": iso_micros(k.last_used_at) if k.last_used_at else None,
        "revoked_at": iso_micros(k.revoked_at) if k.revoked_at else None,
    }


# --- Workspace settings (admin) ---

_SETTING_FIELDS = {
    "min_threshold": (0.5, 0.99),
    "min_audit_sample_rate": (0.01, 0.5),
    "calibration_blind_rate": (0.0, 1.0),
    "breaker_window": None,
    "breaker_min_audits": None,
}


class WorkspaceSettingsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "admin")
        return Response(_settings_dict(self.ctx.workspace.settings))

    def patch(self, request):
        permissions.require(self.ctx, "admin")
        s = self.ctx.workspace.settings
        data = request.data if isinstance(request.data, dict) else {}
        changed: dict = {}
        for field, rng in _SETTING_FIELDS.items():
            if field in data:
                val = data[field]
                if rng and not (rng[0] <= float(val) <= rng[1]):
                    raise unprocessable("input_invalid", f"{field} out of range")
                setattr(s, field, val)
                changed[field] = val
        if "require_four_eyes" in data:
            s.require_four_eyes = bool(data["require_four_eyes"])
            changed["require_four_eyes"] = s.require_four_eyes
        if "reviewer_claim_mode" in data:
            if data["reviewer_claim_mode"] not in ("any", "next_only"):
                raise unprocessable("input_invalid", "invalid reviewer_claim_mode")
            s.reviewer_claim_mode = data["reviewer_claim_mode"]
            changed["reviewer_claim_mode"] = s.reviewer_claim_mode
        if "daily_token_budget" in data:
            s.daily_token_budget = data["daily_token_budget"]
            changed["daily_token_budget"] = s.daily_token_budget

        now = timezone.now()
        with transaction.atomic():
            s.updated_by = self.ctx.username
            s.updated_at = now
            s.save()
            if changed:
                audit.append_many(
                    self.ctx.workspace.id,
                    [
                        Event(
                            "workspace.settings_changed",
                            now,
                            actor_type="user",
                            actor_id=self.ctx.actor_id,
                            payload=changed,
                        )
                    ],
                )
        return Response(_settings_dict(s))


def _settings_dict(s: WorkspaceSettings) -> dict:
    return {
        "min_threshold": s.min_threshold,
        "min_audit_sample_rate": s.min_audit_sample_rate,
        "calibration_blind_rate": s.calibration_blind_rate,
        "require_four_eyes": s.require_four_eyes,
        "reviewer_claim_mode": s.reviewer_claim_mode,
        "daily_token_budget": s.daily_token_budget,
        "breaker_window": s.breaker_window,
        "breaker_min_audits": s.breaker_min_audits,
    }
