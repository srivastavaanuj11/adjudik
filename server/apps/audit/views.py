"""Audit endpoints: events, checkpoints, public key, export, verifications (SPEC §11.2).

All need the ``audit`` capability (admin or auditor, SPEC §15.1).
"""

from __future__ import annotations

from django.http import StreamingHttpResponse
from django.utils import timezone
from rest_framework.response import Response

from apps.common import permissions
from apps.common.clock import iso_micros
from apps.common.errors import bad_request, not_found, unprocessable
from apps.common.views import AdjudikAPIView

from . import service
from .models import AuditCheckpoint, AuditEvent, AuditVerification, VerificationMode

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def _limit(request) -> int:
    raw = request.query_params.get("limit")
    if raw is None:
        return DEFAULT_LIMIT
    try:
        return min(max(1, int(raw)), MAX_LIMIT)
    except ValueError:
        raise bad_request("limit must be an integer") from None


class AuditEventsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "audit")
        from_seq = int(request.query_params.get("from_seq", 1))
        limit = _limit(request)
        rows = list(
            AuditEvent.objects.filter(workspace_id=self.ctx.workspace.id, seq__gte=from_seq)
            .order_by("seq")[: limit + 1]
        )
        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            next_cursor = str(rows[-1].seq + 1)
        return Response(
            {
                "results": [
                    {
                        "seq": r.seq,
                        "event_type": r.event_type,
                        "occurred_at": iso_micros(r.occurred_at),
                        "actor_type": r.actor_type,
                        "actor_id": r.actor_id,
                        "evaluation_id": str(r.evaluation_id) if r.evaluation_id else None,
                        "subject_type": r.subject_type,
                        "subject_id": r.subject_id,
                        "payload": r.payload,
                        "prev_hash": r.prev_hash,
                        "hash": r.hash,
                    }
                    for r in rows
                ],
                "next_cursor": next_cursor,
            }
        )


class AuditCheckpointsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "audit")
        rows = AuditCheckpoint.objects.filter(workspace_id=self.ctx.workspace.id).order_by("-seq")
        return Response(
            {
                "results": [
                    {
                        "seq": c.seq,
                        "hash": c.hash,
                        "created_at": iso_micros(c.created_at),
                        "key_id": c.key_id,
                        "signature": c.signature,
                        "anchored_at": iso_micros(c.anchored_at) if c.anchored_at else None,
                    }
                    for c in rows
                ]
            }
        )


class AuditPublicKeyView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "audit")
        return Response({"key_id": service.key_id(), "public_key": service.public_key_b64()})


class AuditExportView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "audit")
        from_seq = int(request.query_params.get("from_seq", 1))
        to_seq_raw = request.query_params.get("to_seq")
        to_seq = int(to_seq_raw) if to_seq_raw is not None else None
        now = timezone.now()
        service.record_export(
            self.ctx.workspace.id,
            from_seq,
            to_seq,
            actor=(self.ctx.actor_type, self.ctx.actor_id),
            now=now,
        )
        stream = service.export_lines(self.ctx.workspace.id, from_seq, to_seq, now)
        response = StreamingHttpResponse(stream, content_type="application/x-ndjson")
        response["Content-Disposition"] = "attachment; filename=audit-export.ndjson"
        return response


class AuditVerificationsView(AdjudikAPIView):
    def post(self, request):
        permissions.require(self.ctx, "audit")
        data = request.data if isinstance(request.data, dict) else {}
        mode = data.get("mode", VerificationMode.INCREMENTAL)
        if mode not in VerificationMode.values:
            raise unprocessable("bad_request", "invalid verification mode")
        verification = service.run_verification(
            self.ctx.workspace.id,
            mode,
            requested_by=self.ctx.username if self.ctx.actor_type == "user" else None,
            now=timezone.now(),
        )
        return Response(_verification_dict(verification), status=201)


class AuditVerificationDetailView(AdjudikAPIView):
    def get(self, request, verification_id):
        permissions.require(self.ctx, "audit")
        v = AuditVerification.objects.filter(
            workspace_id=self.ctx.workspace.id, id=verification_id
        ).first()
        if v is None:
            raise not_found("verification")
        return Response(_verification_dict(v))


class AuditVerificationLatestView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "audit")
        v = (
            AuditVerification.objects.filter(workspace_id=self.ctx.workspace.id)
            .order_by("-created_at")
            .first()
        )
        if v is None:
            return Response({"verification": None})
        return Response(_verification_dict(v))


def _verification_dict(v: AuditVerification) -> dict:
    return {
        "id": str(v.id),
        "mode": v.mode,
        "status": v.status,
        "ok": v.ok,
        "from_seq": v.from_seq,
        "head_seq": v.head_seq,
        "first_bad_seq": v.first_bad_seq,
        "mismatches": v.mismatches,
        "error": v.error,
        "created_at": iso_micros(v.created_at),
        "finished_at": iso_micros(v.finished_at) if v.finished_at else None,
    }
