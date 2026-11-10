"""Metrics endpoint (SPEC §11.2, §11.7).

``GET /decisions/{key}/metrics`` needs ``read``. Responses are cached in Redis for
60 s per (decision, window, version).
"""

from __future__ import annotations

import json

from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response

from apps.common import permissions
from apps.common.errors import not_found, unprocessable
from apps.common.views import AdjudikAPIView
from apps.decisions.models import Decision

from . import service

CACHE_TTL = 60


class MetricsView(AdjudikAPIView):
    def get(self, request, key):
        permissions.require(self.ctx, "read")
        decision = Decision.objects.filter(workspace=self.ctx.workspace, key=key).first()
        if decision is None or not self.ctx.can_see_decision(decision.id):
            raise not_found("decision")

        now = timezone.now()
        try:
            start, end, version = service.resolve_window(request.query_params, now=now)
        except (ValueError, TypeError) as exc:
            raise unprocessable("input_invalid", "invalid from/to/version") from exc

        ckey = f"metrics:{decision.id}:{start.date()}:{end.date()}:{version}"
        cached = cache.get(ckey)
        if cached is not None:
            return Response(json.loads(cached))
        body = service.compute(decision, start=start, end=end, version=version, now=now)
        cache.set(ckey, json.dumps(body), timeout=CACHE_TTL)
        return Response(body)
