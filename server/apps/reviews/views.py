"""Review queue endpoints (SPEC §11.2, §8.5, §8.6).

Reading needs ``reviews_read`` (admin, reviewer or auditor); working a task needs
``reviews`` (admin or reviewer). A reviewer sees a case only through
``GET /reviews/{id}`` for a task they hold, completed, or could claim (SPEC §8.6),
so blind tasks never leak through ``/evaluations``.
"""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response

from apps.common import permissions
from apps.common.errors import not_found, unprocessable
from apps.common.pagination import paginate
from apps.common.views import AdjudikAPIView

from . import service
from .models import ReviewKind, ReviewStatus, ReviewTask


def _readable_for(ctx, qs):
    """Restrict a reviews queryset to what the caller may read (SPEC §8.6, §11.2)."""
    if ctx.decision_ids is not None:
        qs = qs.filter(decision_id__in=ctx.decision_ids)
    if ctx.role == "reviewer":
        # Own (claimed or completed) plus claimable (open, or an expired claim).
        now = timezone.now()
        qs = qs.filter(
            Q(claimed_by_id=ctx.actor_id)
            | Q(completed_by_id=ctx.actor_id)
            | Q(status=ReviewStatus.OPEN)
            | Q(status=ReviewStatus.CLAIMED, lease_expires_at__lte=now)
        )
    return qs


class ReviewsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "reviews_read")
        qs = ReviewTask.objects.select_related(
            "evaluation", "decision", "workspace__settings"
        ).filter(workspace=self.ctx.workspace)
        qs = _readable_for(self.ctx, qs)

        params = request.query_params
        if params.get("status"):
            qs = qs.filter(status=params["status"])
        if params.get("kind"):
            qs = qs.filter(kind=params["kind"])
        if params.get("decision"):
            qs = qs.filter(decision__key=params["decision"])
        if params.get("reason"):
            qs = qs.filter(reason=params["reason"])
        if params.get("evaluation"):
            qs = qs.filter(evaluation_id=params["evaluation"])

        qs = qs.order_by("priority", "due_at", "created_at")
        rows, next_cursor = paginate(qs, request)
        return Response({"results": [service.summary(t) for t in rows], "next_cursor": next_cursor})


class ReviewDetailView(AdjudikAPIView):
    def get(self, request, task_id):
        permissions.require(self.ctx, "reviews_read")
        task = _resolve_readable(self.ctx, task_id)
        return Response(service.detail(task))


def _resolve_readable(ctx, task_id) -> ReviewTask:
    task = (
        ReviewTask.objects.select_related(
            "evaluation", "evaluation__decision_version", "decision", "workspace__settings"
        )
        .filter(workspace=ctx.workspace, id=task_id)
        .first()
    )
    if task is None or not ctx.can_see_decision(task.decision_id):
        raise not_found("review")
    if ctx.role == "reviewer":
        now = timezone.now()
        own = str(task.claimed_by_id) == str(ctx.actor_id) or str(task.completed_by_id) == str(
            ctx.actor_id
        )
        claimable = task.status == ReviewStatus.OPEN or (
            task.status == ReviewStatus.CLAIMED
            and task.lease_expires_at is not None
            and task.lease_expires_at <= now
        )
        if not (own or claimable):
            raise not_found("review")
    return task


class ReviewNextView(AdjudikAPIView):
    def post(self, request):
        permissions.require(self.ctx, "reviews")
        data = request.data if isinstance(request.data, dict) else {}
        kind = data.get("kind")
        if kind is not None and kind not in (ReviewKind.GATE, ReviewKind.AUDIT):
            raise unprocessable("review_invalid", "kind must be gate or audit")
        task = service.claim_next(
            self.ctx, decision_key=data.get("decision"), kind=kind, now=timezone.now()
        )
        if task is None:
            return Response(status=204)
        return Response(service.detail(task))


class ReviewClaimView(AdjudikAPIView):
    def post(self, request, task_id):
        permissions.require(self.ctx, "reviews")
        task = service.claim_by_id(self.ctx, task_id, now=timezone.now())
        return Response(service.detail(task))


class ReviewHeartbeatView(AdjudikAPIView):
    def post(self, request, task_id):
        permissions.require(self.ctx, "reviews")
        task = service.heartbeat(self.ctx, task_id, now=timezone.now())
        return Response(service.detail(task))


class ReviewReleaseView(AdjudikAPIView):
    def post(self, request, task_id):
        permissions.require(self.ctx, "reviews")
        task = service.release(self.ctx, task_id, now=timezone.now())
        return Response(service.detail(task))


class ReviewCompleteView(AdjudikAPIView):
    def post(self, request, task_id):
        permissions.require(self.ctx, "reviews")
        data = request.data if isinstance(request.data, dict) else {}
        task = service.complete(self.ctx, task_id, data, now=timezone.now())
        return Response(service.detail(task))
