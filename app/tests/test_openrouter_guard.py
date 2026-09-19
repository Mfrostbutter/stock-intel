"""Every OpenRouter chat request carries provider.data_collection=deny, or it never leaves the box."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import HumanMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.analyst import openrouter  # noqa: E402


def _ok_response(request: httpx.Request) -> httpx.Response:
    body = {"id": "x", "model": "anthropic/claude-sonnet-5", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12, "cost": 0.00004}}
    return httpx.Response(200, json=body, request=request)


def test_every_chat_body_carries_deny_and_usage():
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return _ok_response(request)

    client = openrouter.make_http_client(transport=httpx.MockTransport(handler))
    model = openrouter.chat_model("anthropic/claude-sonnet-5", "test-key", http_client=client)
    resp = model.invoke([HumanMessage("ping")])
    assert seen and seen[0]["provider"] == {"data_collection": "deny"}
    assert seen[0]["usage"] == {"include": True}
    assert "reasoning" not in seen[0]
    assert resp.response_metadata["token_usage"]["cost"] == pytest.approx(0.00004)
    assert resp.usage_metadata["input_tokens"] == 10


def test_request_without_deny_is_refused_before_sending():
    sent = []
    client = openrouter.make_http_client(transport=httpx.MockTransport(lambda r: sent.append(r) or _ok_response(r)))
    with pytest.raises(openrouter.DataPolicyViolation):
        client.post(f"{openrouter.OPENROUTER_BASE}/chat/completions", json={"model": "x", "messages": []})
    with pytest.raises(openrouter.DataPolicyViolation):
        client.post(f"{openrouter.OPENROUTER_BASE}/chat/completions",
                    json={"model": "x", "messages": [], "provider": {"data_collection": "allow"}})
    assert sent == []


def test_extra_cannot_override_provider():
    seen = []
    client = openrouter.make_http_client(transport=httpx.MockTransport(lambda r: seen.append(json.loads(r.content)) or _ok_response(r)))
    model = openrouter.chat_model("openai/gpt-5.6-luna", "k", http_client=client,
                                  extra={"provider": {"data_collection": "allow"}, "reasoning": {"effort": "low"}})
    model.invoke([HumanMessage("x")])
    assert seen[0]["provider"] == {"data_collection": "deny"}
    assert seen[0]["reasoning"] == {"effort": "low"}


def test_bare_ids_and_empty_keys_are_rejected():
    with pytest.raises(ValueError):
        openrouter.chat_model("claude-sonnet-5", "k")
    with pytest.raises(RuntimeError):
        openrouter.chat_model("anthropic/claude-sonnet-5", "")
