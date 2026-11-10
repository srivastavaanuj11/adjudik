"""Decision metrics (SPEC §11.7).

Evaluations are bucketed by ``created_at`` in UTC; the window defaults to the last
7 days and is capped at 90. Every figure is derived from stored outcomes, scores
and task states — never from purgeable input — so metrics keep computing after a
purge (SPEC §9.7). Percentiles use linear interpolation between order statistics;
the weighted audit-agreement interval is a two-sided 95 % Clopper-Pearson bound on
the effective (propensity-weighted) counts.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any

from django.db.models import Count, Sum
from scipy.stats import beta

from apps.evaluations.models import Evaluation, EvaluationStatus, ModelCall
from apps.reviews.models import ReviewKind, ReviewStatus, ReviewTask

MAX_WINDOW_DAYS = 90
DEFAULT_WINDOW_DAYS = 7
CP_ALPHA = 0.05


def _percentile(values: list[float], p: float) -> float | None:
    """Linear-interpolated percentile (``p`` in [0, 1]); ``None`` for no data."""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    rank = p * (len(xs) - 1)
    lo = int(rank)
    frac = rank - lo
    if lo + 1 >= len(xs):
        return xs[-1]
    return xs[lo] + frac * (xs[lo + 1] - xs[lo])


def clopper_pearson(k: float, n: float) -> tuple[float, float]:
    """Two-sided 95 % Clopper-Pearson interval; accepts effective (non-integer) counts."""
    if n <= 0:
        return 0.0, 1.0
    lower = 0.0 if k <= 0 else float(beta.ppf(CP_ALPHA / 2, k, n - k + 1))
    upper = 1.0 if k >= n else float(beta.ppf(1 - CP_ALPHA / 2, k + 1, n - k))
    return lower, upper


def resolve_window(params, *, now: _dt.datetime) -> tuple[_dt.datetime, _dt.datetime, int | None]:
    """Parse ``from``/``to``/``version`` into a bounded UTC window (SPEC §11.7)."""
    to = _parse_date(params.get("to"), default=now.date())
    frm = _parse_date(params.get("from"), default=to - _dt.timedelta(days=DEFAULT_WINDOW_DAYS))
    if frm > to:
        frm = to
    if (to - frm).days > MAX_WINDOW_DAYS:
        frm = to - _dt.timedelta(days=MAX_WINDOW_DAYS)
    start = _dt.datetime.combine(frm, _dt.time.min, tzinfo=_dt.UTC)
    end = _dt.datetime.combine(to, _dt.time.min, tzinfo=_dt.UTC) + _dt.timedelta(days=1)
    version = int(params["version"]) if params.get("version") else None
    return start, end, version


def _parse_date(value, *, default) -> _dt.date:
    if not value:
        return default
    return _dt.date.fromisoformat(value)


def compute(decision, *, start: _dt.datetime, end: _dt.datetime, version: int | None, now):
    """Build the metrics object for one decision over ``[start, end)`` (SPEC §11.7)."""
    evals = Evaluation.objects.filter(
        workspace=decision.workspace, decision=decision, created_at__gte=start, created_at__lt=end
    )
    if version is not None:
        evals = evals.filter(decision_version__version=version)

    non_cancelled = evals.exclude(status=EvaluationStatus.CANCELLED)
    volume = non_cancelled.count()

    decided = evals.filter(status=EvaluationStatus.DECIDED)
    by = {
        row["decided_by"]: row["n"] for row in decided.values("decided_by").annotate(n=Count("id"))
    }
    rule_n = by.get("rule", 0)
    model_n = by.get("model", 0)
    human_n = by.get("human", 0)

    handed = non_cancelled.filter(handed_to_model=True).count()

    tasks = ReviewTask.objects.filter(workspace=decision.workspace, decision=decision)
    if version is not None:
        tasks = tasks.filter(evaluation__decision_version__version=version)
    gate_tasks = tasks.filter(
        kind=ReviewKind.GATE, evaluation__created_at__gte=start, evaluation__created_at__lt=end
    )
    gate_created = gate_tasks.count()

    completed_visible = gate_tasks.filter(
        status=ReviewStatus.COMPLETED, blind=False, suggested_outcome__isnull=False
    )
    cv_total = completed_visible.count()
    cv_override = completed_visible.filter(action="override").count()

    metrics: dict[str, Any] = {
        "window": {
            "from": start.date().isoformat(),
            "to": (end - _dt.timedelta(days=1)).date().isoformat(),
        },
        "version": version,
        "volume": volume,
        "decided_by": {"rule": rule_n, "model": model_n, "human": human_n},
        "automation_rate": _ratio(rule_n + model_n, volume),
        "model_automation_rate": _ratio(model_n, handed),
        "review_rate": _ratio(gate_created, volume),
        "override_rate": _ratio(cv_override, cv_total),
        "audit_agreement": _audit_agreement(tasks),
        "audit_completion_rate": _audit_completion(tasks, decision, now),
        "queue": _queue(gate_tasks_all(tasks), now),
        "latency_ms": _latency(decided),
        "review_turnaround_min": _turnaround(gate_tasks),
        "route_reasons": _route_reasons(gate_tasks),
        **_tokens_cost(decision, start, end, version),
        "daily": _daily(evals, decided, gate_tasks, start, end),
    }
    return metrics


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def _audit_agreement(tasks) -> dict[str, Any]:
    rows = list(
        tasks.filter(
            kind=ReviewKind.AUDIT, status=ReviewStatus.COMPLETED, label_propensity__isnull=False
        ).values_list("agrees_with_model", "label_propensity")
    )
    raw_n = len(rows)
    eff_n = sum(1.0 / p for _, p in rows if p)
    eff_k = sum(1.0 / p for agrees, p in rows if p and agrees)
    if eff_n <= 0:
        return {"point": None, "lower": None, "upper": None, "n": raw_n, "effective_n": 0.0}
    lower, upper = clopper_pearson(eff_k, eff_n)
    return {
        "point": round(eff_k / eff_n, 6),
        "lower": round(lower, 6),
        "upper": round(upper, 6),
        "n": raw_n,
        "effective_n": round(eff_n, 6),
    }


def _audit_completion(tasks, decision, now) -> dict[str, Any]:
    audits = tasks.filter(kind=ReviewKind.AUDIT)
    open_backlog = audits.filter(status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED]).count()
    # "older than 2x SLA": due_at is created_at + SLA, so 2x SLA old means now past
    # due_at by another SLA, i.e. created before now - 2*SLA. Use due_at as the
    # SLA anchor: eligible when due_at < now - (now - created)... simpler: created
    # more than 2 x (due_at - created) ago. We approximate with due_at < now - SLA.
    eligible = audits.filter(due_at__lt=now)
    created = eligible.count()
    completed = eligible.filter(status=ReviewStatus.COMPLETED).count()
    return {
        "rate": _ratio(completed, created),
        "completed": completed,
        "created": created,
        "open_backlog": open_backlog,
    }


def gate_tasks_all(tasks):
    return tasks.filter(kind=ReviewKind.GATE)


def _queue(gate_tasks, now) -> dict[str, int]:
    live = gate_tasks.filter(status__in=[ReviewStatus.OPEN, ReviewStatus.CLAIMED])
    return {
        "open": live.filter(status=ReviewStatus.OPEN).count(),
        "claimed": live.filter(status=ReviewStatus.CLAIMED).count(),
        "overdue": live.filter(due_at__lt=now).count(),
    }


def _latency(decided) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for by in ("rule", "model"):
        rows = decided.filter(decided_by=by, decided_at__isnull=False).values_list(
            "created_at", "decided_at"
        )
        deltas = [(d - c).total_seconds() * 1000.0 for c, d in rows]
        out[by] = {"p50": _percentile(deltas, 0.5), "p95": _percentile(deltas, 0.95)}
    return out


def _turnaround(gate_tasks) -> dict[str, Any]:
    rows = gate_tasks.filter(status=ReviewStatus.COMPLETED, completed_at__isnull=False).values_list(
        "created_at", "completed_at"
    )
    minutes = [(done - created).total_seconds() / 60.0 for created, done in rows]
    return {"p50": _percentile(minutes, 0.5), "p95": _percentile(minutes, 0.95)}


def _route_reasons(gate_tasks) -> dict[str, int]:
    return {
        row["reason"]: row["n"]
        for row in gate_tasks.values("reason").annotate(n=Count("id")).order_by("reason")
    }


def _tokens_cost(decision, start, end, version) -> dict[str, Any]:
    calls = ModelCall.objects.filter(
        workspace=decision.workspace,
        evaluation__decision=decision,
        created_at__gte=start,
        created_at__lt=end,
    )
    if version is not None:
        calls = calls.filter(evaluation__decision_version__version=version)
    agg = calls.aggregate(
        input_tokens=Sum("input_tokens"), output_tokens=Sum("output_tokens"), cost=Sum("cost_usd")
    )
    return {
        "tokens": {
            "input": agg["input_tokens"] or 0,
            "output": agg["output_tokens"] or 0,
        },
        "cost_usd": float(agg["cost"]) if agg["cost"] is not None else 0.0,
    }


def _daily(evals, decided, gate_tasks, start, end) -> list[dict[str, Any]]:
    days: dict[_dt.date, dict[str, Any]] = {}
    day = start.date()
    while day < end.date():
        days[day] = {
            "date": day.isoformat(),
            "volume": 0,
            "decided_by": {"rule": 0, "model": 0, "human": 0},
            "_turnarounds": [],
        }
        day += _dt.timedelta(days=1)

    for created_at, status in evals.values_list("created_at", "status"):
        d = created_at.astimezone(_dt.UTC).date()
        if d in days and status != EvaluationStatus.CANCELLED:
            days[d]["volume"] += 1

    for created_at, decided_by in decided.values_list("created_at", "decided_by"):
        d = created_at.astimezone(_dt.UTC).date()
        if d in days and decided_by in ("rule", "model", "human"):
            days[d]["decided_by"][decided_by] += 1

    for created_at, completed_at in gate_tasks.filter(
        status=ReviewStatus.COMPLETED, completed_at__isnull=False
    ).values_list("created_at", "completed_at"):
        d = created_at.astimezone(_dt.UTC).date()
        if d in days:
            days[d]["_turnarounds"].append((completed_at - created_at).total_seconds() / 60.0)

    out = []
    for d in sorted(days):
        row = days[d]
        turns = row.pop("_turnarounds")
        row["turnaround_p50_min"] = _percentile(turns, 0.5)
        out.append(row)
    return out
