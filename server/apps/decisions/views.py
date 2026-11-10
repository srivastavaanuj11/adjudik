"""Decision, version and policy endpoints (SPEC §11.2, §11.8).

Reads need ``read``; drafts/validate/dry-run/render-prompt need ``manage``;
publish/retire/acknowledge-model and the policy lifecycle are admin-session only
(SPEC §15.1). A key scoped to ``decision_ids`` gets ``404`` for other decisions.
"""

from __future__ import annotations

import random
from typing import Any

from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response

from adjudik_core import AutomationPolicy as CoreAutomationPolicy
from adjudik_core import EvaluationContext, InputValidationError, load_definition
from adjudik_core.model_step import build_prompt
from apps.common import audit, permissions
from apps.common.audit import Event
from apps.common.clock import iso_micros
from apps.common.engine import build_engine
from apps.common.errors import conflict, not_found, rate_limited, unprocessable
from apps.common.pagination import paginate
from apps.common.views import AdjudikAPIView

from . import service
from .models import AutomationPolicy, Decision, DecisionVersion, PolicyStatus, VersionStatus

DRY_RUN_HOURLY_LIMIT = 20


# --- lookups ---


def _resolve_decision(ctx, key: str) -> Decision:
    decision = Decision.objects.filter(workspace=ctx.workspace, key=key).first()
    if decision is None or not ctx.can_see_decision(decision.id):
        raise not_found("decision")
    return decision


def _resolve_version(ctx, key: str, n: int) -> tuple[Decision, DecisionVersion]:
    decision = _resolve_decision(ctx, key)
    version = DecisionVersion.objects.filter(decision=decision, version=n).first()
    if version is None:
        raise not_found("version")
    return decision, version


def _core_policy(db_policy: AutomationPolicy | None, *, audit_rate: float | None = None):
    if db_policy is None:
        return None
    rate = audit_rate if audit_rate is not None else db_policy.audit_sample_rate
    return CoreAutomationPolicy(
        thresholds=dict(db_policy.thresholds),
        audit_sample_rate=rate,
        targets=dict(db_policy.targets) if db_policy.targets else None,
        calibration_map=db_policy.calibration_map,
    )


# --- decisions ---


class DecisionsView(AdjudikAPIView):
    def get(self, request):
        permissions.require(self.ctx, "read")
        qs = Decision.objects.filter(workspace=self.ctx.workspace)
        if self.ctx.decision_ids is not None:
            qs = qs.filter(id__in=self.ctx.decision_ids)
        rows, next_cursor = paginate(qs, request)
        return Response({"results": [self._summary(d) for d in rows], "next_cursor": next_cursor})

    def post(self, request):
        permissions.require(self.ctx, "manage")
        data = request.data if isinstance(request.data, dict) else {}
        key = data.get("key")
        name = data.get("name")
        description = data.get("description", "")
        if not isinstance(key, str) or not key:
            raise unprocessable("input_invalid", "key is required")
        if not isinstance(name, str) or not name:
            raise unprocessable("input_invalid", "name is required")
        if Decision.objects.filter(workspace=self.ctx.workspace, key=key).exists():
            raise conflict("invalid_state", "a decision with that key already exists")
        now = timezone.now()
        from django.db import transaction

        with transaction.atomic():
            decision = Decision.objects.create(
                workspace=self.ctx.workspace,
                key=key,
                name=name,
                description=description or "",
                created_by=self.ctx.username,
                created_at=now,
            )
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "decision.created",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        subject_type="decision",
                        subject_id=str(decision.id),
                        payload={"decision_id": str(decision.id), "key": key},
                    )
                ],
            )
        return Response(self._summary(decision), status=201)

    @staticmethod
    def _summary(d: Decision) -> dict[str, Any]:
        return {
            "id": str(d.id),
            "key": d.key,
            "name": d.name,
            "description": d.description,
            "legal_hold": d.legal_hold,
            "created_at": iso_micros(d.created_at),
        }


class DecisionDetailView(AdjudikAPIView):
    def get(self, request, key):
        permissions.require(self.ctx, "read")
        decision = _resolve_decision(self.ctx, key)
        versions = DecisionVersion.objects.filter(decision=decision).order_by("-version")
        body = DecisionsView._summary(decision)
        body["versions"] = [service.version_summary(v) for v in versions]
        return Response(body)


# --- versions ---


class VersionsView(AdjudikAPIView):
    def get(self, request, key):
        permissions.require(self.ctx, "read")
        decision = _resolve_decision(self.ctx, key)
        versions = DecisionVersion.objects.filter(decision=decision).order_by("-version")
        return Response({"results": [service.version_summary(v) for v in versions]})

    def post(self, request, key):
        permissions.require(self.ctx, "manage")
        decision = _resolve_decision(self.ctx, key)
        parsed = service.parse_body(request.data, request.content_type or "")
        if parsed.doc.get("key") not in (None, key):
            raise unprocessable(
                "definition_invalid",
                "definition key does not match the decision",
                details=[
                    {"path": "/key", "code": "key_mismatch", "message": "key must match the URL"}
                ],
            )
        compiled = service.compile_or_422(parsed.doc)
        _, warnings = service.issue_details(parsed.doc)
        now = timezone.now()
        version = service.create_draft(
            decision=decision,
            parsed=parsed,
            compiled=compiled,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(
            {
                "version": version.version,
                "status": version.status,
                "definition_hash": version.definition_hash,
                "rules_hash": version.rules_hash or None,
                "warnings": warnings,
            },
            status=201,
        )


class VersionDetailView(AdjudikAPIView):
    def get(self, request, key, n):
        permissions.require(self.ctx, "read")
        _, version = _resolve_version(self.ctx, key, n)
        return Response(service.version_detail(version))

    def put(self, request, key, n):
        permissions.require(self.ctx, "manage")
        _, version = _resolve_version(self.ctx, key, n)
        if version.status != VersionStatus.DRAFT:
            raise conflict("version_not_draft", "only a draft may be replaced")
        parsed = service.parse_body(request.data, request.content_type or "")
        if parsed.doc.get("key") not in (None, key):
            raise unprocessable("definition_invalid", "definition key does not match the decision")
        compiled = service.compile_or_422(parsed.doc)
        _, warnings = service.issue_details(parsed.doc)
        now = timezone.now()
        version = service.replace_draft(
            version=version,
            parsed=parsed,
            compiled=compiled,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(
            {
                "version": version.version,
                "status": version.status,
                "definition_hash": version.definition_hash,
                "rules_hash": version.rules_hash or None,
                "warnings": warnings,
            }
        )


class PublishView(AdjudikAPIView):
    def post(self, request, key, n):
        permissions.require(self.ctx, "publish")
        _, version = _resolve_version(self.ctx, key, n)
        data = request.data if isinstance(request.data, dict) else {}
        carry_over = bool(data.get("carry_over_policy", False))
        now = timezone.now()
        version = service.publish_version(
            version=version,
            carry_over_policy=carry_over,
            require_four_eyes=self.ctx.workspace.settings.require_four_eyes,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(service.version_detail(version))


class RetireView(AdjudikAPIView):
    def post(self, request, key, n):
        permissions.require(self.ctx, "publish")
        _, version = _resolve_version(self.ctx, key, n)
        now = timezone.now()
        version = service.retire_version(
            version=version,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            now=now,
        )
        return Response(service.version_detail(version))


class AcknowledgeModelView(AdjudikAPIView):
    def post(self, request, key, n):
        permissions.require(self.ctx, "publish")
        _, version = _resolve_version(self.ctx, key, n)
        now = timezone.now()
        version = service.acknowledge_model(
            version=version,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            now=now,
        )
        return Response(service.version_detail(version))


# --- validate / dry-run / render-prompt ---


class ValidateView(AdjudikAPIView):
    def post(self, request):
        permissions.require(self.ctx, "manage")
        parsed = service.parse_body(request.data, request.content_type or "")
        errors, warnings = service.issue_details(parsed.doc)
        return Response({"valid": not errors, "errors": errors, "warnings": warnings})


class RenderPromptView(AdjudikAPIView):
    def post(self, request, key, n):
        permissions.require(self.ctx, "manage")
        _, version = _resolve_version(self.ctx, key, n)
        compiled = load_definition(version.definition)
        if compiled.definition.model_step is None:
            raise unprocessable("definition_invalid", "this version has no model_step")
        data = request.data if isinstance(request.data, dict) else {}
        input_data = data.get("input")
        engine = build_engine()
        try:
            engine.validate_input(compiled, input_data)
        except InputValidationError as exc:
            raise unprocessable(
                "input_invalid",
                "input is invalid",
                details=[
                    {"code": i.code, "path": i.path, "message": i.message} for i in exc.issues
                ],
            ) from exc
        minimized = engine.minimize_input(compiled, input_data)
        system, user = build_prompt(compiled.definition, minimized)
        return Response({"system": system, "user": user})


class DryRunView(AdjudikAPIView):
    def post(self, request, key, n):
        permissions.require(self.ctx, "manage")
        _, version = _resolve_version(self.ctx, key, n)
        self._rate_limit()
        data = request.data if isinstance(request.data, dict) else {}
        input_data = data.get("input")
        skip_model = bool(data.get("skip_model", False))

        compiled = load_definition(version.definition)
        db_policy = AutomationPolicy.objects.filter(
            decision_version=version, status=PolicyStatus.ACTIVE
        ).first()
        # Draw no audit or blind sample: suppress sampling and blind draws.
        core_policy = _core_policy(db_policy, audit_rate=0.0)
        ctx = EvaluationContext(blind_rate=0.0)
        # Deterministic rng for a preview; no tasks are stored anyway.
        engine = build_engine(rng=random.Random(0))

        try:
            engine.validate_input(compiled, input_data)
        except InputValidationError as exc:
            raise unprocessable(
                "input_invalid",
                "input is invalid",
                details=[
                    {"code": i.code, "path": i.path, "message": i.message} for i in exc.issues
                ],
            ) from exc

        body = self._run(engine, compiled, input_data, core_policy, ctx, skip_model)
        now = timezone.now()
        model = body.get("model") or {}
        from django.db import transaction

        with transaction.atomic():
            audit.append_many(
                self.ctx.workspace.id,
                [
                    Event(
                        "decision.dry_run",
                        now,
                        actor_type=self.ctx.actor_type,
                        actor_id=self.ctx.actor_id,
                        subject_type="decision_version",
                        subject_id=str(version.id),
                        payload={
                            "decision_id": str(version.decision_id),
                            "version": version.version,
                            "model_calls": model.get("calls", 0),
                            "input_tokens": model.get("input_tokens", 0),
                            "output_tokens": model.get("output_tokens", 0),
                        },
                    )
                ],
            )
        return Response(body)

    def _rate_limit(self) -> None:
        ckey = f"dry_run:{self.ctx.workspace.id}:{self.ctx.actor_id}"
        count = cache.get(ckey, 0)
        if count >= DRY_RUN_HOURLY_LIMIT:
            raise rate_limited("dry-run limit is 20 per hour")
        cache.set(ckey, count + 1, timeout=3600)

    def _run(self, engine, compiled, input_data, core_policy, ctx, skip_model) -> dict[str, Any]:
        rule_result = engine.evaluate_rules(compiled, input_data)
        _, trace = compiled.evaluate_rules(input_data)
        base: dict[str, Any] = {
            "rule_result": {
                "type": rule_result.type,
                "matched_row_ids": rule_result.matched_row_ids,
                "row_id": rule_result.row_id,
                "outcome": rule_result.outcome,
            },
            "rule_trace": trace,
            "screen_flags": [],
            "model": None,
            "warnings": [],
        }
        from adjudik_core.engine import _rule_handled_without_model

        if _rule_handled_without_model(compiled.definition, rule_result):
            routing = engine.route(compiled, rule_result, None, core_policy, ctx)
            return {**base, **self._routing_fields(routing, None)}

        minimized = engine.minimize_input(compiled, input_data)
        pre = engine.pre_model_check(compiled, minimized, ctx)
        base["screen_flags"] = pre.flags
        if pre.gate_reason is not None:
            return {
                **base,
                "status": "needs_review",
                "outcome": None,
                "decided_by": None,
                "route_reason": pre.gate_reason,
            }
        if skip_model:
            return {
                **base,
                "status": "needs_review",
                "outcome": None,
                "decided_by": None,
                "route_reason": "model_skipped",
            }
        model_result = engine.run_model_step(
            compiled, minimized, deadline=engine.clock.monotonic() + engine.step_deadline_s
        )
        routing = engine.route(compiled, rule_result, model_result, core_policy, ctx)
        base["model"] = self._model_dict(model_result)
        return {**base, **self._routing_fields(routing, model_result)}

    @staticmethod
    def _routing_fields(routing, model_result) -> dict[str, Any]:
        from adjudik_core.engine import Decided

        if isinstance(routing, Decided):
            return {
                "status": "decided",
                "outcome": routing.outcome,
                "decided_by": routing.by,
                "route_reason": None,
            }
        return {
            "status": "needs_review",
            "outcome": None,
            "decided_by": None,
            "route_reason": routing.reason,
        }

    @staticmethod
    def _model_dict(m) -> dict[str, Any]:
        input_tokens = sum(c.input_tokens or 0 for c in m.calls)
        output_tokens = sum(c.output_tokens or 0 for c in m.calls)
        return {
            "ok": m.ok,
            "suggested_outcome": m.outcome,
            "raw_confidence": m.raw_confidence,
            "rationale": m.rationale,
            "calls": len(m.calls),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }


# --- policies ---


class VersionPoliciesView(AdjudikAPIView):
    def get(self, request, key, n):
        permissions.require(self.ctx, "read")
        _, version = _resolve_version(self.ctx, key, n)
        policies = AutomationPolicy.objects.filter(decision_version=version).order_by(
            "-proposed_at"
        )
        return Response({"results": [service.policy_detail(p) for p in policies]})

    def post(self, request, key, n):
        permissions.require(self.ctx, "policy")
        _, version = _resolve_version(self.ctx, key, n)
        compiled = load_definition(version.definition)
        outcome_ids = {o.id for o in compiled.definition.outcomes}
        settings = self.ctx.workspace.settings
        data = request.data if isinstance(request.data, dict) else {}
        thresholds, rate, targets = service.validate_policy_fields(
            thresholds=data.get("thresholds", {}),
            audit_sample_rate=data.get("audit_sample_rate"),
            targets=data.get("targets"),
            outcome_ids=outcome_ids,
            min_threshold=settings.min_threshold,
            min_audit_sample_rate=settings.min_audit_sample_rate,
        )
        note = data.get("note", "")
        if not isinstance(note, str) or len(note) > 500:
            raise unprocessable("policy_invalid", "note must be a string of at most 500 chars")
        now = timezone.now()
        policy = service.propose_policy(
            version=version,
            thresholds=thresholds,
            audit_sample_rate=rate,
            targets=targets,
            note=note,
            require_four_eyes=settings.require_four_eyes,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(service.policy_detail(policy), status=201)


class VersionActivePolicyView(AdjudikAPIView):
    def get(self, request, key, n):
        permissions.require(self.ctx, "read")
        _, version = _resolve_version(self.ctx, key, n)
        policy = AutomationPolicy.objects.filter(
            decision_version=version, status=PolicyStatus.ACTIVE
        ).first()
        if policy is None:
            return Response({"policy": None})
        return Response(service.policy_detail(policy))


def _resolve_policy(ctx, policy_id) -> AutomationPolicy:
    policy = AutomationPolicy.objects.filter(workspace=ctx.workspace, id=policy_id).first()
    if policy is None:
        raise not_found("policy")
    return policy


class PolicyApproveView(AdjudikAPIView):
    def post(self, request, policy_id):
        permissions.require(self.ctx, "policy")
        policy = _resolve_policy(self.ctx, policy_id)
        now = timezone.now()
        policy = service.approve_policy(
            policy=policy,
            require_four_eyes=self.ctx.workspace.settings.require_four_eyes,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(service.policy_detail(policy))


class PolicyRejectView(AdjudikAPIView):
    def post(self, request, policy_id):
        permissions.require(self.ctx, "policy")
        policy = _resolve_policy(self.ctx, policy_id)
        now = timezone.now()
        policy = service.reject_policy(
            policy=policy,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            actor_name=self.ctx.username,
            now=now,
        )
        return Response(service.policy_detail(policy))


class PolicyDeactivateView(AdjudikAPIView):
    def post(self, request, policy_id):
        permissions.require(self.ctx, "policy")
        policy = _resolve_policy(self.ctx, policy_id)
        now = timezone.now()
        policy = service.deactivate_policy(
            policy=policy,
            actor_type=self.ctx.actor_type,
            actor_id=self.ctx.actor_id,
            now=now,
        )
        return Response(service.policy_detail(policy))
