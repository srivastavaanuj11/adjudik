"""Providers: keyword_fake formula, fake double, build_providers, mocked HTTP (SPEC §7.2)."""

from __future__ import annotations

import hashlib

import httpx
import pytest

from adjudik_core.providers import (
    AnthropicProvider,
    FakeProvider,
    KeywordFakeProvider,
    OpenAICompatibleProvider,
    ProviderConfigError,
    ProviderError,
    build_providers,
)
from adjudik_core.providers.base import ModelRequest

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "outcome": {"type": "string", "enum": ["approve", "deny"]},
        "confidence": {"type": "number"},
    },
}
NO_CONF_SCHEMA = {
    "type": "object",
    "properties": {
        "rationale": {"type": "string"},
        "outcome": {"type": "string", "enum": ["approve", "deny"]},
    },
}


def _req(user: str, schema: dict = OUTPUT_SCHEMA) -> ModelRequest:
    return ModelRequest(
        model="m",
        system="s",
        user=user,
        output_schema=schema,
        temperature=0,
        max_output_tokens=600,
        timeout_s=20,
    )


# --- keyword_fake: exact formula (SPEC §7.2) ---


def _expected(user: str, keywords: dict[str, list[str]], enum: list[str]) -> tuple[str, float, str]:
    best_o, best_hits, best_words = enum[0], -1, []  # type: ignore[var-annotated]
    for o in enum:
        found = sorted({w for w in keywords.get(o, []) if w.lower() in user.lower()})
        if len(found) > best_hits:
            best_hits, best_o, best_words = len(found), o, found
    offset = int.from_bytes(hashlib.sha256(user.encode()).digest()[:8], "big") / 2**64 * 0.1 - 0.05
    conf = min(0.99, max(0.01, 0.55 + 0.1 * best_hits + offset))
    rationale = "Matched: " + ", ".join(best_words) if best_words else "No keywords matched."
    return best_o, conf, rationale


def test_keyword_fake_matches_the_documented_formula() -> None:
    kw = {"approve": ["damaged", "broken"], "deny": ["used", "fine"]}
    provider = KeywordFakeProvider(kw)
    user = "the item arrived DAMAGED and broken on delivery"
    resp = provider.complete(_req(user))
    import json

    payload = json.loads(resp.text)
    exp_o, exp_c, exp_r = _expected(user, kw, ["approve", "deny"])
    assert payload["outcome"] == exp_o == "approve"
    assert payload["confidence"] == pytest.approx(exp_c)
    assert payload["rationale"] == exp_r == "Matched: broken, damaged"
    assert resp.stop_reason == "end"
    assert resp.model_reported == "m"


def test_keyword_fake_ties_go_to_first_in_enum_order() -> None:
    kw = {"approve": ["x"], "deny": ["x"]}
    provider = KeywordFakeProvider(kw)
    payload = __import__("json").loads(provider.complete(_req("x marks it")).text)
    assert payload["outcome"] == "approve"


def test_keyword_fake_no_match_rationale_and_floor() -> None:
    provider = KeywordFakeProvider({"approve": ["zzz"], "deny": ["qqq"]})
    payload = __import__("json").loads(provider.complete(_req("nothing here")).text)
    assert payload["rationale"] == "No keywords matched."
    assert 0.01 <= payload["confidence"] <= 0.99


def test_keyword_fake_omits_confidence_without_the_field() -> None:
    provider = KeywordFakeProvider({"approve": ["a"], "deny": ["b"]})
    payload = __import__("json").loads(provider.complete(_req("a", NO_CONF_SCHEMA)).text)
    assert "confidence" not in payload


# --- fake double ---


def test_fake_replays_and_raises() -> None:
    err = ProviderError("boom", code="timeout")
    from adjudik_core.providers.base import ModelResponse

    ok = ModelResponse("{}", "end", "m", 1, 1, 0, None)
    provider = FakeProvider([ok, err])
    assert provider.complete(_req("x")) is ok
    with pytest.raises(ProviderError):
        provider.complete(_req("x"))
    with pytest.raises(ProviderError, match="exhausted"):
        provider.complete(_req("x"))


def test_fake_callable() -> None:
    from adjudik_core.providers.base import ModelResponse

    provider = FakeProvider(lambda r: ModelResponse(r.user, "end", "m", 1, 1, 0, None))
    assert provider.complete(_req("hello")).text == "hello"


# --- build_providers ---


def test_build_providers_default_has_demo_and_anthropic() -> None:
    providers = build_providers()
    assert set(providers) == {"anthropic", "demo"}
    assert isinstance(providers["demo"], KeywordFakeProvider)
    assert isinstance(providers["anthropic"], AnthropicProvider)


def test_build_providers_from_json_string_and_env(monkeypatch) -> None:
    cfg = '{"x": {"type": "openai_compatible", "base_url": "http://h", "deployment": "d"}}'
    providers = build_providers(cfg)
    assert isinstance(providers["x"], OpenAICompatibleProvider)
    monkeypatch.setenv("ADJUDIK_PROVIDERS", cfg)
    assert "x" in build_providers()


def test_build_providers_rejects_bad_config() -> None:
    with pytest.raises(ProviderConfigError):
        build_providers("not json")
    with pytest.raises(ProviderConfigError):
        build_providers({"x": {"type": "nope"}})
    with pytest.raises(ProviderConfigError):
        build_providers({"x": 5})


def test_provider_error_rejects_unknown_code() -> None:
    with pytest.raises(ValueError, match="unknown provider error code"):
        ProviderError("x", code="made_up")


# --- anthropic provider: mocked HTTP only (httpx.MockTransport) ---


def _anthropic_client(handler):
    import anthropic
    import httpx2

    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )


def _anthropic_json(status: int, body: dict):
    import httpx2

    return lambda request: httpx2.Response(status, json=body)


ANTHROPIC_OK = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": '{"rationale":"r","outcome":"approve","confidence":0.9}'}],
    "model": "claude-haiku-5-5",
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 11, "output_tokens": 7},
}


def test_anthropic_success() -> None:
    provider = AnthropicProvider(client=_anthropic_client(_anthropic_json(200, ANTHROPIC_OK)))
    resp = provider.complete(_req("the item is damaged"))
    assert resp.stop_reason == "end"
    assert resp.model_reported == "claude-haiku-5-5"
    assert resp.input_tokens == 11 and resp.output_tokens == 7
    assert '"outcome":"approve"' in resp.text


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(429, "rate_limited", True), (500, "server_error", True), (400, "bad_request", False)],
)
def test_anthropic_error_mapping(status, code, retryable) -> None:
    client = _anthropic_client(_anthropic_json(status, {"error": {"message": "x"}}))
    with pytest.raises(ProviderError) as info:
        AnthropicProvider(client=client).complete(_req("x"))
    assert info.value.code == code
    assert info.value.retryable is retryable


def test_anthropic_timeout_is_retryable() -> None:
    import httpx2

    def handler(request):
        raise httpx2.TimeoutException("slow")

    with pytest.raises(ProviderError) as info:
        AnthropicProvider(client=_anthropic_client(handler)).complete(_req("x"))
    assert info.value.code == "timeout" and info.value.retryable


def test_anthropic_connection_error_is_retryable() -> None:
    import httpx2

    def handler(request):
        raise httpx2.ConnectError("down")

    with pytest.raises(ProviderError) as info:
        AnthropicProvider(client=_anthropic_client(handler)).complete(_req("x"))
    assert info.value.code == "connection" and info.value.retryable


def test_anthropic_missing_key_is_auth_error(monkeypatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderError) as info:
        AnthropicProvider().complete(_req("x"))
    assert info.value.code == "auth"


def test_anthropic_config_fingerprint_excludes_keys() -> None:
    fp = AnthropicProvider(base_url="https://proxy").config_fingerprint
    assert fp == {"type": "anthropic", "base_url": "https://proxy", "deployment": None}


# --- openai-compatible provider: mocked HTTP only (httpx.MockTransport) ---


def _openai_json(status: int, body: dict):
    return lambda request: httpx.Response(status, json=body)


def _openai_client(handler):
    import openai

    return openai.OpenAI(
        api_key="test-key",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def _openai_body(content: str, finish_reason: str = "stop") -> dict:
    return {
        "id": "cmpl_1",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-x",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13},
    }


def test_openai_success() -> None:
    body = _openai_body('{"rationale":"r","outcome":"deny","confidence":0.7}')
    provider = OpenAICompatibleProvider(
        deployment="gpt-x", client=_openai_client(_openai_json(200, body))
    )
    resp = provider.complete(_req("x"))
    assert resp.stop_reason == "end"
    assert resp.input_tokens == 9 and resp.output_tokens == 4
    assert '"outcome":"deny"' in resp.text


def test_openai_length_maps_to_max_tokens() -> None:
    body = _openai_body("{", finish_reason="length")
    provider = OpenAICompatibleProvider(client=_openai_client(_openai_json(200, body)))
    assert provider.complete(_req("x")).stop_reason == "max_tokens"


def test_openai_error_mapping() -> None:
    client = _openai_client(_openai_json(404, {"error": {"message": "no"}}))
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleProvider(client=client).complete(_req("x"))
    assert info.value.code == "not_found" and not info.value.retryable


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [(429, "rate_limited", True), (500, "server_error", True), (401, "auth", False)],
)
def test_openai_error_mapping_statuses(status, code, retryable) -> None:
    client = _openai_client(_openai_json(status, {"error": {"message": "x"}}))
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleProvider(client=client).complete(_req("x"))
    assert info.value.code == code and info.value.retryable is retryable


def test_openai_timeout_and_connection_are_retryable() -> None:
    def timeout(request):
        raise httpx.TimeoutException("slow")

    def conn(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError) as t:
        OpenAICompatibleProvider(client=_openai_client(timeout)).complete(_req("x"))
    assert t.value.code == "timeout"
    with pytest.raises(ProviderError) as c:
        OpenAICompatibleProvider(client=_openai_client(conn)).complete(_req("x"))
    assert c.value.code == "connection"


def test_openai_missing_key_is_auth_error(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError) as info:
        OpenAICompatibleProvider().complete(_req("x"))
    assert info.value.code == "auth"


def test_openai_config_fingerprint_and_base_url() -> None:
    fp = OpenAICompatibleProvider(base_url="http://vllm", deployment="llama").config_fingerprint
    assert fp == {"type": "openai_compatible", "base_url": "http://vllm", "deployment": "llama"}


@pytest.mark.parametrize("mode", ["json_object", "none", "json_schema"])
def test_openai_response_format_modes(mode) -> None:
    captured: dict = {}

    def handler(request):
        import json as _json

        captured.update(_json.loads(request.content))
        return httpx.Response(200, json=_openai_body('{"rationale":"r","outcome":"deny"}'))

    provider = OpenAICompatibleProvider(response_format=mode, client=_openai_client(handler))
    provider.complete(_req("x", NO_CONF_SCHEMA))
    if mode == "none":
        assert "response_format" not in captured
    elif mode == "json_object":
        assert captured["response_format"] == {"type": "json_object"}
    else:
        assert captured["response_format"]["type"] == "json_schema"


def test_openai_rejects_bad_response_format() -> None:
    with pytest.raises(ValueError, match="response_format"):
        OpenAICompatibleProvider(response_format="xml")
