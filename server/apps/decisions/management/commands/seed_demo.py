"""``manage.py seed_demo`` (SPEC §16.5).

Builds the ``demo`` workspace with both example decisions, drives the normal
services with an injected clock stepping through the past 30 days, and simulates
reviewers so the review queue, audit chain and metrics all have realistic data.
Refuses to run unless ``DEBUG`` is true. Celery is forced eager so the model
worker runs inline as each case is submitted.
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import random
from pathlib import Path

import yaml
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from adjudik_core import load_definition
from apps.accounts import service as accounts_service
from apps.accounts.models import Membership, Role, UserPasswordState
from apps.common.context import RequestContext
from apps.common.engine import build_engine
from apps.decisions.models import (
    AutomationPolicy,
    Decision,
    DecisionVersion,
    PolicySource,
    PolicyStatus,
    VersionStatus,
)
from apps.evaluations import service as eval_service
from apps.reviews import service as review_service
from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask

EXAMPLES = Path(__file__).resolve().parents[5] / "examples"

SUPPORT_TEMPLATES = [
    (
        "billing",
        "Charged twice",
        "I was billed twice for invoice {n}. Please refund the duplicate.",
    ),
    (
        "billing",
        "Subscription change",
        "I want to downgrade my plan before the next billing cycle.",
    ),
    ("technical", "Export fails", "Clicking export returns a 500 error on report {n}."),
    ("technical", "Broken integration", "The webhook for event {n} stopped firing yesterday."),
    ("account", "Locked out", "I cannot sign in and the reset link for user {n} does not work."),
    ("account", "Permissions", "A teammate needs access to workspace {n} but gets denied."),
    ("shipping", "Late delivery", "Order {n} was due last week and has not arrived."),
    ("shipping", "Wrong address", "Please change the delivery address for order {n}."),
    ("other", "General question", "Just wondering how feature {n} is meant to be used."),
]
LEAVE_OPEN = 20  # keep at least this many gate tasks open (SPEC §16.5)


class Command(BaseCommand):
    help = "Seed the demo workspace with both examples and simulated activity (SPEC §16.5)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--refunds", type=int, default=600)
        parser.add_argument("--support", type=int, default=3500)
        parser.add_argument("--seed", type=int, default=0)
        parser.add_argument(
            "--password",
            default=None,
            help="Fixed password for every demo user (for e2e). Falls back to "
            "$ADJUDIK_SEED_PASSWORD; a random one per user when unset.",
        )

    def handle(self, *args, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("seed_demo refuses to run unless DEBUG is true (SPEC §16.5).")
        # Drive the real services synchronously (SPEC §16.5).
        from adjudik_server.celery import app as celery_app

        celery_app.conf.task_always_eager = True
        celery_app.conf.task_eager_propagates = True

        rng = random.Random(options["seed"])
        start = _dt.datetime.now(tz=_dt.UTC) - _dt.timedelta(days=30)
        self._clock = start
        ws = accounts_service.create_workspace("demo", "Demo", now=start)
        ws.settings.calibration_blind_rate = 1.0  # shadow mode: collect labels fast
        ws.settings.save(update_fields=["calibration_blind_rate"])

        import os

        fixed_password = options.get("password") or os.environ.get("ADJUDIK_SEED_PASSWORD")
        users = self._make_users(ws, start, fixed_password=fixed_password)
        self.stdout.write("Demo users (password):")
        for name, (_user, pw) in users.items():
            self.stdout.write(f"  {name}: {pw}")

        refund_dec, refund_ver = self._publish(ws, "refund_approval", start, with_policy=True)
        support_dec, support_ver = self._publish(ws, "support_routing", start, with_policy=False)

        admin_ctx = self._ctx(ws, users["admin"][0], Role.ADMIN)
        reviewer_ctx = self._ctx(ws, users["reviewer"][0], Role.REVIEWER)

        self._run_refunds(ws, refund_dec, refund_ver, admin_ctx, rng, options["refunds"])
        self._run_support(ws, support_dec, support_ver, admin_ctx, rng, options["support"])
        self._simulate_reviewers(ws, reviewer_ctx, rng)

        open_gate = ReviewTask.objects.filter(
            workspace=ws, kind=ReviewKind.GATE, status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED]
        ).count()
        self.stdout.write(self.style.SUCCESS(f"Seeded demo: {open_gate} open gate tasks remain."))

    # --- setup ---

    def _tick(self, minutes: int = 7) -> _dt.datetime:
        self._clock += _dt.timedelta(minutes=minutes)
        return self._clock

    def _make_users(self, ws, now, *, fixed_password: str | None = None) -> dict:
        import secrets

        model = get_user_model()
        out: dict = {}
        # A second admin (admin2) lets the four-eyes approval flow be exercised.
        for name, role in (
            ("admin", Role.ADMIN),
            ("admin2", Role.ADMIN),
            ("author", Role.AUTHOR),
            ("reviewer", Role.REVIEWER),
            ("auditor", Role.AUDITOR),
        ):
            pw = fixed_password or secrets.token_urlsafe(12)
            user = model.objects.create_user(username=f"demo-{name}", password=pw)
            Membership.objects.create(workspace=ws, user=user, role=role, created_at=now)
            UserPasswordState.objects.update_or_create(user=user, defaults={"must_change": False})
            out[name] = (user, pw)
        return out

    def _ctx(self, ws, user, role) -> RequestContext:
        return RequestContext(
            workspace=ws,
            actor_type="user",
            actor_id=str(user.id),
            role=role,
            username=user.get_username(),
        )

    def _publish(self, ws, example: str, now, *, with_policy: bool):
        doc = yaml.safe_load((EXAMPLES / example / "decision.yaml").read_text())
        doc["model_step"]["provider"] = "demo"
        doc["model_step"]["model"] = "demo-1"
        compiled = load_definition(doc)
        decision = Decision.objects.create(
            workspace=ws,
            key=compiled.key,
            name=compiled.definition.name,
            description=compiled.definition.description or "",
            created_at=now,
        )
        version = DecisionVersion.objects.create(
            workspace=ws,
            decision=decision,
            version=1,
            status=VersionStatus.ACTIVE,
            definition=compiled.definition.model_dump(mode="json"),
            source_text=yaml.safe_dump(doc),
            source_format="yaml",
            definition_hash=compiled.definition_hash,
            rules_hash=compiled.rules_hash or "",
            model_step_fingerprint=build_engine().fingerprint(compiled),
            created_at=now,
            updated_at=now,
            published_at=now,
        )
        if with_policy:
            policy = yaml.safe_load((EXAMPLES / example / "policy.yaml").read_text())
            AutomationPolicy.objects.create(
                workspace=ws,
                decision_version=version,
                status=PolicyStatus.ACTIVE,
                thresholds=policy["thresholds"],
                targets=policy.get("targets", {}),
                audit_sample_rate=policy["audit_sample_rate"],
                source=PolicySource.MANUAL,
                proposed_at=now,
                activated_at=now,
            )
        return decision, version

    # --- traffic ---

    def _submit(self, ws, decision, version, ctx, data, rng):
        # A bad synthetic case should not stop the seed.
        with contextlib.suppress(Exception):
            eval_service.submit(
                decision=decision,
                version=version,
                ctx=ctx,
                input_data=data,
                external_id=None,
                idempotency_key=None,
                metadata={"channel": "seed"},
                now=self._tick(),
                rng=random.Random(rng.random()),
            )

    def _run_refunds(self, ws, decision, version, ctx, rng, count: int) -> None:
        reasons = ["damaged", "not_as_described", "changed_mind", "late", "other"]
        statuses = ["delivered", "shipped", "cancelled"]
        for i in range(count):
            data = {
                "order": {
                    "id": f"A-{i}",
                    "amount": round(rng.uniform(10, 400), 2),
                    "currency": "USD",
                    "status": rng.choice(statuses),
                    "days_since_delivery": rng.randint(0, 40),
                },
                "customer": {
                    "id": f"C-{i}",
                    "tier": rng.choice(["standard", "gold"]),
                    "refunds_last_90d": rng.randint(0, 5),
                },
                "request": {
                    "reason_code": rng.choice(reasons),
                    "reason_text": "the item arrived damaged and i would like a refund please",
                },
            }
            self._submit(ws, decision, version, ctx, data, rng)

    def _run_support(self, ws, decision, version, ctx, rng, count: int) -> None:
        for i in range(count):
            _queue, subject, body = rng.choice(SUPPORT_TEMPLATES)
            data = {
                "ticket": {
                    "id": f"T-{i}",
                    "subject": subject,
                    "body": body.format(n=i),
                    "form": "contact",
                    "plan": rng.choice(["free", "pro", "enterprise"]),
                }
            }
            self._submit(ws, decision, version, ctx, data, rng)

    # --- simulated reviewers (hidden ground truth, SPEC §16.5) ---

    def _simulate_reviewers(self, ws, ctx, rng) -> None:
        pending = list(
            ReviewTask.objects.filter(
                workspace=ws, status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED]
            ).order_by("created_at")
        )
        # Leave the most recent LEAVE_OPEN gate tasks open so the queue has work.
        gate = [t for t in pending if t.kind == ReviewKind.GATE]
        to_complete = (
            pending
            if len(gate) <= LEAVE_OPEN
            else [
                t
                for t in pending
                if not (t.kind == ReviewKind.GATE and t in set(gate[-LEAVE_OPEN:]))
            ]
        )
        for task in to_complete:
            self._complete_with_ground_truth(ws, ctx, task, rng)

    def _complete_with_ground_truth(self, ws, ctx, task, rng) -> None:
        evaluation = task.evaluation
        defn = load_definition(evaluation.decision_version.definition)
        outcomes = [o.id for o in defn.definition.outcomes]
        suggestion = task.suggested_outcome
        conf = evaluation.raw_confidence or 0.5
        # Agreement rises with confidence and exceeds 0.99 for scores >= 0.9.
        p_agree = min(0.999, 0.5 + 0.55 * conf)
        if suggestion and rng.random() < p_agree:
            outcome, action = suggestion, "accept"
        else:
            others = [o for o in outcomes if o != suggestion] or outcomes
            outcome, action = rng.choice(others), "override"

        blind = task.blind or suggestion is None
        body = {
            "action": "decide" if blind else action,
            "outcome": outcome,
            "reason_codes": [],
            "reason_text": "ground-truth label" if (action == "override" and not blind) else None,
        }
        # Declared reason codes: pick one if the definition requires them.
        declared = [c.id for c in defn.definition.review.reason_codes]
        if declared:
            body["reason_codes"] = [rng.choice(declared)]
        # Skip any task that raced to a terminal state.
        with contextlib.suppress(Exception):
            review_service.claim_by_id(ctx, task.id, now=self._tick(1))
            review_service.complete(ctx, task.id, body, now=self._tick(1))
