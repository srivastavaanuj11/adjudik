"""Session lifetime middleware (SPEC §15.2).

Enforces an 8 h absolute ceiling and a 30 min idle timeout on authenticated
sessions. Django's ``SESSION_COOKIE_AGE`` covers the absolute ceiling at the
cookie level; this adds the idle window and a hard server-side absolute check.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.contrib.auth import logout

_START_KEY = "_adjudik_session_start"
_LAST_KEY = "_adjudik_session_last"


class SessionTimeoutMiddleware:
    def __init__(self, get_response) -> None:
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and hasattr(request, "session"):
            now = int(time.time())
            start = request.session.get(_START_KEY)
            last = request.session.get(_LAST_KEY)
            idle = getattr(settings, "SESSION_IDLE_TIMEOUT", 1800)
            absolute = getattr(settings, "SESSION_COOKIE_AGE", 28800)
            if start is None:
                request.session[_START_KEY] = now
                request.session[_LAST_KEY] = now
            elif (now - start) > absolute or (last is not None and (now - last) > idle):
                logout(request)
            else:
                request.session[_LAST_KEY] = now
        response = self.get_response(request)
        csp = getattr(settings, "ADJUDIK_CSP", "")
        if csp and "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = csp
        return response
