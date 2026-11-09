"""Test-double and demo providers (SPEC §7.2).

``FakeProvider`` replays a scripted list of responses/errors (or a callable) and is
used throughout the tests. ``KeywordFakeProvider`` is the deterministic ``demo``
provider: it needs no network and no API key, so the README quickstart and
``--provider demo`` run anywhere.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any

from .base import ModelRequest, ModelResponse, ProviderError

__all__ = ["FakeProvider", "KeywordFakeProvider"]


class FakeProvider:
    """Replays scripted ``ModelResponse``/``ProviderError`` items, or a callable."""

    def __init__(
        self,
        script: Sequence[ModelResponse | ProviderError]
        | Callable[[ModelRequest], ModelResponse | ProviderError],
        *,
        name: str = "fake",
    ) -> None:
        self.name = name
        self.config_fingerprint: dict[str, Any] = {"type": "fake"}
        self._script = script
        self._index = 0

    def complete(self, request: ModelRequest) -> ModelResponse:
        item: ModelResponse | ProviderError
        if callable(self._script):
            item = self._script(request)
        else:
            if self._index >= len(self._script):
                raise ProviderError("fake script exhausted", code="server_error")
            item = self._script[self._index]
            self._index += 1
        if isinstance(item, ProviderError):
            raise item
        return item


class KeywordFakeProvider:
    """Deterministic keyword-scoring demo provider (SPEC §7.2)."""

    def __init__(self, keywords: dict[str, list[str]], *, name: str = "demo") -> None:
        self.name = name
        self.config_fingerprint: dict[str, Any] = {"type": "keyword_fake"}
        self._keywords = {o: list(words) for o, words in keywords.items()}

    def complete(self, request: ModelRequest) -> ModelResponse:
        enum = self._outcome_enum(request.output_schema)
        user = request.user
        user_lower = user.lower()

        best_outcome = enum[0]
        best_hits = -1
        best_words: list[str] = []
        for outcome in enum:  # enum order => ties go to the first (SPEC §7.2)
            found = sorted({w for w in self._keywords.get(outcome, []) if w.lower() in user_lower})
            if len(found) > best_hits:
                best_hits = len(found)
                best_outcome = outcome
                best_words = found

        digest = hashlib.sha256(user.encode("utf-8")).digest()[:8]
        offset = int.from_bytes(digest, "big") / 2**64 * 0.1 - 0.05
        confidence = min(0.99, max(0.01, 0.55 + 0.1 * best_hits + offset))
        rationale = "Matched: " + ", ".join(best_words) if best_words else "No keywords matched."

        payload: dict[str, Any] = {"rationale": rationale, "outcome": best_outcome}
        if "confidence" in request.output_schema.get("properties", {}):
            payload["confidence"] = confidence
        text = json.dumps(payload)

        return ModelResponse(
            text=text,
            stop_reason="end",
            model_reported=request.model,
            input_tokens=_estimate_tokens(request.system) + _estimate_tokens(user),
            output_tokens=_estimate_tokens(text),
            latency_ms=0,
            request_id=None,
        )

    @staticmethod
    def _outcome_enum(output_schema: dict[str, Any]) -> list[str]:
        try:
            enum = output_schema["properties"]["outcome"]["enum"]
        except (KeyError, TypeError) as exc:  # pragma: no cover - defensive
            raise ProviderError(
                "keyword_fake needs an outcome enum in the output schema",
                code="bad_request",
            ) from exc
        if not isinstance(enum, list) or not enum:  # pragma: no cover - defensive
            raise ProviderError("keyword_fake outcome enum is empty", code="bad_request")
        return [str(o) for o in enum]


def _estimate_tokens(text: str) -> int:
    # A coarse, deterministic stand-in; the demo provider has no real tokeniser.
    return max(1, len(text) // 4)
