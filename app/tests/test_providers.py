"""Provider registry validation, model references and catalog normalisation. No DB, no network."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel import providers  # noqa: E402


def test_parse_ref_defaults_to_openrouter():
    assert providers.parse_ref("anthropic/claude-sonnet-5") == ("openrouter", "anthropic/claude-sonnet-5")
    assert providers.parse_ref("claude-sonnet-5") == ("openrouter", "claude-sonnet-5")
    assert providers.parse_ref("anthropic-direct:claude-sonnet-5") == ("anthropic-direct", "claude-sonnet-5")
    assert providers.parse_ref("local:qwen3:32b") == ("local", "qwen3:32b")


def test_validate_accepts_defaults_and_rejects_bad_input():
    clean = providers.validate(providers.DEFAULTS)
    assert clean[0]["id"] == "openrouter" and clean[0]["key_env"] == "OPENROUTER_API_KEY"
    with pytest.raises(ValueError, match="at least one provider"):
        providers.validate([])
    with pytest.raises(ValueError, match="kind"):
        providers.validate([{"id": "xx", "kind": "mystery"}])
    with pytest.raises(ValueError, match="duplicate"):
        providers.validate([{"id": "aa", "kind": "openai"}, {"id": "aa", "kind": "openai"}])
    with pytest.raises(ValueError, match="https"):
        providers.validate([{"id": "aa", "kind": "openai", "base_url": "http://example.com/v1"}])
    with pytest.raises(ValueError, match="UPPER_SNAKE"):
        providers.validate([{"id": "aa", "kind": "openai", "key_env": "lower"}])
    with pytest.raises(ValueError, match="keys are not stored"):
        providers.validate([{"id": "aa", "kind": "openai", "api_key": "sk-x"}])


def test_validate_fills_kind_defaults_and_allows_local_http():
    clean = providers.validate([{"id": "anthropic-direct", "name": "Anthropic", "kind": "anthropic"},
                                {"id": "local", "kind": "openai", "base_url": "http://localhost:11434/v1", "key_env": "LOCAL_KEY"}])
    assert clean[0]["base_url"] == "https://api.anthropic.com/v1" and clean[0]["key_env"] == "ANTHROPIC_API_KEY"
    assert clean[1]["base_url"] == "http://localhost:11434/v1"
    # A compose service name is a local host too; a public http URL is not.
    assert providers.validate([{"id": "ollama", "kind": "openai", "base_url": "http://ollama:11434/v1",
                                "key_env": "LOCAL_KEY"}])[0]["base_url"] == "http://ollama:11434/v1"


def test_openrouter_catalog_normalisation(monkeypatch):
    payload = {"data": [{"id": "z-ai/glm-5.3", "name": "GLM 5.3", "context_length": 1310720,
                         "pricing": {"prompt": "0.0000014", "completion": "0.0000044"},
                         "supported_parameters": ["tools", "structured_outputs", "reasoning"]},
                        {"id": "x/no-tools", "name": "X", "context_length": 8000, "pricing": {"prompt": "0", "completion": "0"},
                         "supported_parameters": []}]}

    class R:
        def json(self): return payload
    monkeypatch.setattr(providers.requests, "get", lambda *a, **k: R())
    out = providers._fetch_openrouter({"base_url": "https://openrouter.ai/api/v1"}, "")
    assert out[0] == {"id": "z-ai/glm-5.3", "name": "GLM 5.3", "context": 1310720, "price_in": 1.4, "price_out": 4.4,
                      "tools": True, "structured": True, "reasoning": True}
    assert out[1]["tools"] is False and out[1]["price_in"] == 0.0


def test_chat_model_for_needs_a_key(monkeypatch):
    monkeypatch.setattr(providers, "registry", lambda: [dict(providers.DEFAULTS[0])])
    monkeypatch.delenv("OPEN_ROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY is empty"):
        providers.chat_model_for("anthropic/claude-sonnet-5")
    monkeypatch.setenv("OPEN_ROUTER_API_KEY", "k")
    m = providers.chat_model_for("claude-sonnet-5")
    assert m.model_name == "anthropic/claude-sonnet-5"
    assert "reasoning" not in (m.extra_body or {})
    low = providers.chat_model_for("anthropic/claude-sonnet-5", reasoning_effort="low")
    assert low.extra_body["reasoning"] == {"effort": "low"}
    assert low.extra_body["provider"] == {"data_collection": "deny"}
    assert "reasoning" not in providers.chat_model_for("anthropic/claude-sonnet-5", reasoning_effort="none").extra_body
    fast = providers.chat_model_for("anthropic/claude-sonnet-5", provider_prefs={"sort": "throughput", "data_collection": "allow"})
    assert fast.extra_body["provider"] == {"sort": "throughput", "data_collection": "deny"}
    with pytest.raises(KeyError):
        providers.chat_model_for("nope:model")


def test_ollama_kind_needs_no_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    clean = providers.validate([{"id": "local", "kind": "ollama"}])
    assert clean[0]["base_url"].endswith("/v1") and clean[0]["key_env"] == "OLLAMA_API_KEY"
    assert providers.key_for(clean[0]) == "ollama"
    assert providers.with_status(clean)[0]["key_present"] is True


def test_every_kind_has_a_catalog_fetcher():
    assert set(providers._FETCH) == set(providers.KINDS)
