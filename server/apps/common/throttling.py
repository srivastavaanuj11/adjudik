"""Rate limits backed by the Redis cache (SPEC §15.8).

Evaluate is limited per API key (``ADJUDIK_EVAL_RATE``) and per workspace
(``ADJUDIK_WORKSPACE_EVAL_RATE``); every other authenticated API call is limited
per actor (``ADJUDIK_API_RATE``). Exceeding a limit raises DRF's ``Throttled``,
which the error handler renders as ``429`` with ``Retry-After`` (SPEC §11.1).
Rates are read from settings at call time so tests can override them.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework.throttling import SimpleRateThrottle


class _SettingRateThrottle(SimpleRateThrottle):
    setting_name = ""  # subclasses set this

    def __init__(self) -> None:
        # Defer rate parsing to get_rate() so override_settings is honoured.
        self.rate = self.get_rate()
        self.num_requests, self.duration = self.parse_rate(self.rate)

    def get_rate(self) -> str:
        return getattr(settings, self.setting_name)

    def _ctx(self, request):
        return getattr(request, "adjudik", None)


class EvaluateKeyThrottle(_SettingRateThrottle):
    scope = "evaluate_key"
    setting_name = "ADJUDIK_EVAL_RATE"

    def get_cache_key(self, request, view):
        ctx = self._ctx(request)
        if ctx is None or not ctx.is_api_key:
            return None
        return f"throttle:eval:key:{ctx.actor_id}"


class WorkspaceEvalThrottle(_SettingRateThrottle):
    scope = "evaluate_workspace"
    setting_name = "ADJUDIK_WORKSPACE_EVAL_RATE"

    def get_cache_key(self, request, view):
        ctx = self._ctx(request)
        if ctx is None:
            return None
        return f"throttle:eval:ws:{ctx.workspace.id}"


class GeneralApiThrottle(_SettingRateThrottle):
    scope = "api"
    setting_name = "ADJUDIK_API_RATE"

    def get_cache_key(self, request, view):
        # Key on the authenticated principal, which exists after authentication
        # (API key via request.auth, session via request.user). Anonymous and
        # pre-auth requests are not throttled here.
        auth = getattr(request, "auth", None)
        if auth is not None and hasattr(auth, "workspace_id"):
            return f"throttle:api:key:{auth.id}"
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return f"throttle:api:user:{user.pk}"
        return None
