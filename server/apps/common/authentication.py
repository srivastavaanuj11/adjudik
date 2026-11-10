"""Authentication: API key (Bearer) and session (SPEC §11.1, §15.2).

``ApiKeyAuthentication`` parses ``Authorization: Bearer adk_<prefix>_<secret>``,
looks the key up by prefix, constant-time compares the secret hash (honouring a
24 h rotation grace), and builds the :class:`RequestContext`. Session requests
resolve the active workspace from the user's membership.
"""

from __future__ import annotations

import datetime as _dt
import hmac

from django.utils import timezone
from rest_framework import authentication

from apps.accounts.models import ApiKey, Membership
from apps.accounts.service import hash_secret

from .context import RequestContext
from .errors import unauthenticated

_LAST_USED_MIN_INTERVAL = _dt.timedelta(minutes=1)


class ApiKeyPrincipal:
    """A minimal authenticated principal for API-key requests (not a Django user)."""

    is_authenticated = True
    is_active = True

    def __init__(self, api_key: ApiKey) -> None:
        self.api_key = api_key

    def __str__(self) -> str:  # pragma: no cover
        return f"api_key:{self.api_key.prefix}"


class ApiKeyAuthentication(authentication.BaseAuthentication):
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header or not header.startswith(self.keyword + " "):
            return None  # fall through to session auth
        token = header[len(self.keyword) + 1 :].strip()
        parts = token.split("_", 2)
        if len(parts) != 3 or parts[0] != "adk":
            raise unauthenticated("malformed API key")
        _, prefix, secret = parts

        try:
            key = ApiKey.objects.select_related("workspace").get(prefix=prefix)
        except ApiKey.DoesNotExist:
            raise unauthenticated("invalid API key") from None

        now = timezone.now()
        if key.revoked_at is not None and key.revoked_at <= now:
            raise unauthenticated("API key revoked")
        if key.expires_at <= now:
            raise unauthenticated("API key expired")

        candidate = hash_secret(secret)
        ok = hmac.compare_digest(candidate, key.key_hash)
        if (
            not ok
            and key.previous_key_hash
            and key.previous_key_expires_at
            and key.previous_key_expires_at > now
        ):
            ok = hmac.compare_digest(candidate, key.previous_key_hash)
        if not ok:
            raise unauthenticated("invalid API key")

        if key.last_used_at is None or (now - key.last_used_at) >= _LAST_USED_MIN_INTERVAL:
            ApiKey.objects.filter(pk=key.pk).update(last_used_at=now)

        ctx = RequestContext(
            workspace=key.workspace,
            actor_type="api_key",
            actor_id=str(key.id),
            scopes=list(key.scopes or []),
            decision_ids=key.decision_ids,
            api_key=key,
        )
        request.adjudik = ctx
        return (ApiKeyPrincipal(key), key)


def build_session_context(request) -> RequestContext | None:
    """Resolve the session user's active workspace and role (SPEC §11.1).

    Default workspace is the user's oldest membership; switched by
    ``POST /auth/workspace`` which stores the id on the session.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return None

    active_id = request.session.get("active_workspace_id")
    membership = None
    if active_id:
        membership = (
            Membership.objects.select_related("workspace")
            .filter(user=user, workspace_id=active_id)
            .first()
        )
    if membership is None:
        membership = (
            Membership.objects.select_related("workspace")
            .filter(user=user)
            .order_by("created_at")
            .first()
        )
    if membership is None:
        return None

    return RequestContext(
        workspace=membership.workspace,
        actor_type="user",
        actor_id=str(user.id),
        role=membership.role,
        username=user.get_username(),
    )
