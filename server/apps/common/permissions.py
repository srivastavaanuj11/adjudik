"""Capability-based authorization (SPEC §15.1).

A capability is granted when the principal's session role is allowed, or one of
the API key's scopes is allowed. API keys have scopes and no role; session users
have a role and no scopes. ``is_staff``/``is_superuser`` are never consulted.
"""

from __future__ import annotations

from .context import RequestContext
from .errors import forbidden

ADMIN = "admin"
AUTHOR = "author"
REVIEWER = "reviewer"
AUDITOR = "auditor"

EVALUATE = "evaluate"
READ = "read"
READ_SENSITIVE = "read_sensitive"
MANAGE = "manage"

# capability -> (allowed roles, allowed key scopes)
_CAPS: dict[str, tuple[set[str], set[str]]] = {
    # Submit evaluations: only API keys with `evaluate` (no session role, §15.1).
    "evaluate": (set(), {EVALUATE}),
    # Read (non-sensitive).
    "read": ({ADMIN, AUTHOR, AUDITOR}, {READ, READ_SENSITIVE}),
    # Read sensitive (inputs, rationale, model calls).
    "read_sensitive": ({ADMIN, AUTHOR, AUDITOR}, {READ_SENSITIVE}),
    # Create/edit drafts, validate, dry-run, render prompt.
    "manage": ({ADMIN, AUTHOR}, {MANAGE}),
    # Publish/retire/acknowledge: admin session only (no scope grants it).
    "publish": ({ADMIN}, set()),
    # Policies: admin session only.
    "policy": ({ADMIN}, set()),
    # Work reviews: admin or reviewer (session only).
    "reviews": ({ADMIN, REVIEWER}, set()),
    # Audit log read/verify/export: admin or auditor.
    "audit": ({ADMIN, AUDITOR}, set()),
    # Purge, legal hold, settings, keys, members, webhooks: admin only.
    "admin": ({ADMIN}, set()),
}


def has_capability(ctx: RequestContext, capability: str) -> bool:
    allowed_roles, allowed_scopes = _CAPS[capability]
    if ctx.actor_type == "user":
        return ctx.role in allowed_roles
    return bool(set(ctx.scopes) & allowed_scopes)


def require(ctx: RequestContext, capability: str) -> None:
    if not has_capability(ctx, capability):
        raise forbidden(f"missing capability: {capability}")
