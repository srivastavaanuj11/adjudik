"""Base API view: authentication, context and capability gate (SPEC §11, §15.1)."""

from __future__ import annotations

from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from .authentication import ApiKeyAuthentication, build_session_context
from .context import RequestContext
from .errors import unauthenticated
from .throttling import GeneralApiThrottle


class AdjudikAPIView(APIView):
    """Common base: API-key or session auth, with the workspace context attached.

    Set ``login_optional = True`` on subclasses used before authentication
    (login, csrf). Otherwise an unauthenticated request gets ``401``.
    """

    authentication_classes = [ApiKeyAuthentication, SessionAuthentication]
    permission_classes = [AllowAny]  # capability checks are explicit in each view
    throttle_classes = [GeneralApiThrottle]  # 600/min per actor (SPEC §15.8)
    login_optional = False

    def initial(self, request, *args, **kwargs) -> None:
        super().initial(request, *args, **kwargs)
        ctx = getattr(request, "adjudik", None)
        if ctx is None:
            ctx = build_session_context(request)
            if ctx is not None:
                request.adjudik = ctx
        if ctx is None and not self.login_optional:
            raise unauthenticated()

    @property
    def ctx(self) -> RequestContext:
        return self.request.adjudik
