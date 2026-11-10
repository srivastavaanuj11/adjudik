"""Per-request principal and workspace context (SPEC §11.1, §15.1).

Permissions come only from the membership role (session) or the API key's
scopes — never ``is_staff``/``is_superuser`` (SPEC §15.1). The context is built
by the authentication layer and read by the capability checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from apps.accounts.models import ApiKey, Workspace


@dataclass
class RequestContext:
    workspace: Workspace
    actor_type: str  # "user" | "api_key"
    actor_id: str  # user id or api-key id, as a string
    role: str | None = None  # session role, when actor_type == "user"
    scopes: list[str] = field(default_factory=list)  # key scopes, when api_key
    decision_ids: list[str] | None = None  # key's decision allow-list (None = all)
    api_key: ApiKey | None = None
    username: str | None = None

    @property
    def is_api_key(self) -> bool:
        return self.actor_type == "api_key"

    def can_see_decision(self, decision_id) -> bool:
        """A key with ``decision_ids`` set is scoped to those decisions (§11.1)."""
        if self.decision_ids is None:
            return True
        return str(decision_id) in {str(d) for d in self.decision_ids}
