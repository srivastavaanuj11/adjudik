"""Webhook management endpoints (SPEC §11.2, §11.6, admin only).

The secret is returned once, at creation and on rotation, and only its encrypted
form is stored (SPEC §15.3). Endpoint changes and rotations are audited; the raw
URL is never put in an event (only its hash).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import secrets as _secrets

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response

from apps.common import audit, crypto, permissions
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.errors import not_found, unprocessable
from apps.common.pagination import paginate
from apps.common.views import AdjudikAPIView
from apps.decisions.models import Decision

from .models import DeliveryStatus, WebhookDelivery, WebhookEndpoint, WebhookEventType

_EVENT_TYPES = {t.value for t in WebhookEventType}
ROTATION_GRACE = _dt.timedelta(hours=24)


def _url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def _new_secret() -> str:
    return f"whsec_{_secrets.token_urlsafe(32)}"


def _validate_event_types(value) -> list[str]:
    if not isinstance(value, list) or not value:
        raise unprocessable("webhook_invalid", "event_types must be a non-empty list")
    for t in value:
        if t not in _EVENT_TYPES:
            raise unprocessable("webhook_invalid", f"unknown event type: {t}")
    return list(dict.fromkeys(value))


def _validate_url(url) -> str:
    if not isinstance(url, str) or not url:
        raise unprocessable("webhook_invalid", "url is required")
    if not url.startswith("https://") and not settings.ADJUDIK_ALLOW_INSECURE_WEBHOOKS:
        raise unprocessable("webhook_invalid", "url must be https")
    return url


def _endpoint_dict(e: WebhookEndpoint) -> dict:
    return {
        "id": str(e.id),
        "url": e.url,
        "decision": e.decision.key if e.decision_id else None,
        "event_types": e.event_types,
        "active": e.active,
        "created_at": iso_micros(e.created_at),
    }


def _resolve_decision_id(ctx, key):
    if not key:
        return None
    decision = Decision.objects.filter(workspace=ctx.workspace, key=key).first()
    if decision is None:
        raise unprocessable("webhook_invalid", "unknown decision")
    return decision


def _changed_event(ctx, endpoint, now) -> Event:
    return Event(
        "webhook_endpoint.changed",
        now,
        actor_type=ctx.actor_type,
        actor_id=ctx.actor_id,
        subject_type="webhook_endpoint",
        subject_id=str(endpoint.id),
        payload={
            "endpoint_id": str(endpoint.id),
            "active": endpoint.active,
            "url_hash": _url_hash(endpoint.url),
            "event_types": endpoint.event_types,
        },
    )


class WebhooksView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "admin")
        qs = WebhookEndpoint.objects.filter(workspace=self.ctx.workspace).order_by("-created_at")
        rows, next_cursor = paginate(qs, request)
        return Response({"results": [_endpoint_dict(e) for e in rows], "next_cursor": next_cursor})

    def post(self, request):
        permissions.require(self.ctx, "admin")
        data = request.data if isinstance(request.data, dict) else {}
        url = _validate_url(data.get("url"))
        event_types = _validate_event_types(data.get("event_types"))
        decision = _resolve_decision_id(self.ctx, data.get("decision"))
        active = bool(data.get("active", True))
        secret = _new_secret()
        now = timezone.now()
        with transaction.atomic():
            endpoint = WebhookEndpoint.objects.create(
                workspace=self.ctx.workspace,
                decision=decision,
                url=url,
                secret_encrypted=crypto.encrypt(secret),
                event_types=event_types,
                active=active,
                created_by=self.ctx.username,
                created_at=now,
            )
            audit.append_many(self.ctx.workspace.id, [_changed_event(self.ctx, endpoint, now)])
        body = _endpoint_dict(endpoint)
        body["secret"] = secret  # returned once
        return Response(body, status=201)


class WebhookDetailView(AdjudikAPIView):
    def patch(self, request, endpoint_id):
        permissions.require(self.ctx, "admin")
        endpoint = _resolve_endpoint(self.ctx, endpoint_id)
        data = request.data if isinstance(request.data, dict) else {}
        if "url" in data:
            endpoint.url = _validate_url(data["url"])
        if "event_types" in data:
            endpoint.event_types = _validate_event_types(data["event_types"])
        if "active" in data:
            endpoint.active = bool(data["active"])
        if "decision" in data:
            endpoint.decision = _resolve_decision_id(self.ctx, data["decision"])
        now = timezone.now()
        with transaction.atomic():
            endpoint.save()
            audit.append_many(self.ctx.workspace.id, [_changed_event(self.ctx, endpoint, now)])
        return Response(_endpoint_dict(endpoint))


class WebhookRotateSecretView(AdjudikAPIView):
    def post(self, request, endpoint_id):
        permissions.require(self.ctx, "admin")
        endpoint = _resolve_endpoint(self.ctx, endpoint_id)
        now = timezone.now()
        new_secret = _new_secret()
        with transaction.atomic():
            endpoint.previous_secret_encrypted = endpoint.secret_encrypted
            endpoint.previous_secret_expires_at = now + ROTATION_GRACE
            endpoint.secret_encrypted = crypto.encrypt(new_secret)
            endpoint.save(
                update_fields=[
                    "previous_secret_encrypted",
                    "previous_secret_expires_at",
                    "secret_encrypted",
                ]
            )
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "webhook_endpoint.secret_rotated",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        subject_type="webhook_endpoint",
                        subject_id=str(endpoint.id),
                        payload={"endpoint_id": str(endpoint.id)},
                    )
                ],
            )
        body = _endpoint_dict(endpoint)
        body["secret"] = new_secret
        return Response(body)


class WebhookDeliveriesView(AdjudikAPIView):
    def get(self, request, endpoint_id):
        permissions.require(self.ctx, "admin")
        endpoint = _resolve_endpoint(self.ctx, endpoint_id)
        qs = WebhookDelivery.objects.filter(
            workspace=self.ctx.workspace, endpoint=endpoint
        ).order_by("-created_at")
        if request.query_params.get("status"):
            qs = qs.filter(status=request.query_params["status"])
        rows, next_cursor = paginate(qs, request)
        return Response({"results": [_delivery_dict(d) for d in rows], "next_cursor": next_cursor})


class WebhookDeliveryRetryView(AdjudikAPIView):
    def post(self, request, delivery_id):
        permissions.require(self.ctx, "admin")
        from . import service

        delivery = WebhookDelivery.objects.filter(
            workspace=self.ctx.workspace, id=delivery_id
        ).first()
        if delivery is None:
            raise not_found("delivery")
        if delivery.status not in (DeliveryStatus.FAILED, DeliveryStatus.DEAD):
            raise unprocessable("webhook_invalid", "only a failed or dead delivery can be retried")
        service.retry_delivery(delivery, now=timezone.now())
        return Response(_delivery_dict(delivery))


def _resolve_endpoint(ctx, endpoint_id) -> WebhookEndpoint:
    endpoint = WebhookEndpoint.objects.filter(workspace=ctx.workspace, id=endpoint_id).first()
    if endpoint is None:
        raise not_found("webhook")
    return endpoint


def _delivery_dict(d: WebhookDelivery) -> dict:
    return {
        "id": str(d.id),
        "endpoint_id": str(d.endpoint_id),
        "event_id": str(d.event_id),
        "event_type": d.event_type,
        "evaluation_id": str(d.evaluation_id) if d.evaluation_id else None,
        "status": d.status,
        "attempt": d.attempt,
        "last_status_code": d.last_status_code,
        "last_error_class": d.last_error_class,
        "next_attempt_at": iso_micros(d.next_attempt_at) if d.next_attempt_at else None,
        "created_at": iso_micros(d.created_at),
        "delivered_at": iso_micros(d.delivered_at) if d.delivered_at else None,
    }
