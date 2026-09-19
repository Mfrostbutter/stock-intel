"""OpenRouter chat model factory. One of four provider kinds; see providers.py.

Every chat request must carry provider.data_collection = "deny". A request-level httpx hook
inspects the outgoing body and refuses to send one without it, so no code path can build a
request that lets a provider train on the content.
"""

from __future__ import annotations

import json
import logging
import threading
import time

import httpx
import requests

log = logging.getLogger("stockintel.analyst.openrouter")

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MODELS_URL = f"{OPENROUTER_BASE}/models"
REQUIRED_PROVIDER = {"data_collection": "deny"}
APP_TITLE = "stock-intel analyst"


class DataPolicyViolation(RuntimeError):
    """Raised before the request leaves the box."""


def _check_request(request: httpx.Request) -> None:
    if not request.url.path.endswith("/chat/completions"):
        return
    try:
        body = json.loads(request.content or b"{}")
    except ValueError as e:
        raise DataPolicyViolation("chat request body is not JSON") from e
    provider = body.get("provider") or {}
    if provider.get("data_collection") != "deny":
        raise DataPolicyViolation("chat request lacks provider.data_collection=deny; refusing to send")


def make_http_client(transport: httpx.BaseTransport | None = None, timeout: float = 180.0) -> httpx.Client:
    """httpx client with the deny check on every request. transport is for tests."""
    return httpx.Client(event_hooks={"request": [_check_request]}, transport=transport, timeout=timeout)


# Reasoning models spend output tokens thinking before the JSON. Budget stays large and uncapped
# until average usage per analysis is known; a structured call that hits the limit returns no content.
DEFAULT_MAX_TOKENS = 32000


def chat_model(model_id: str, api_key: str, temperature: float = 0.2, max_tokens: int = DEFAULT_MAX_TOKENS,
               http_client: httpx.Client | None = None, extra: dict | None = None):
    """ChatOpenAI pointed at OpenRouter. extra merges into the request body (reasoning knobs for the bake-off)."""
    from langchain_openai import ChatOpenAI

    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is empty")
    if "/" not in model_id:
        raise ValueError(f"'{model_id}' is not an OpenRouter id (<vendor>/<model>)")
    body = {"provider": dict(REQUIRED_PROVIDER), "usage": {"include": True}}
    if extra:
        body.update({k: v for k, v in extra.items() if k != "provider"})
        prefs = extra.get("provider") or {}
        body["provider"] = {**{k: v for k, v in prefs.items() if k != "data_collection"}, **REQUIRED_PROVIDER}
    return ChatOpenAI(
        model=model_id,
        api_key=api_key,
        base_url=OPENROUTER_BASE,
        temperature=temperature,
        max_tokens=max_tokens,
        extra_body=body,
        default_headers={"X-Title": APP_TITLE},
        http_client=http_client or make_http_client(),
        max_retries=2,
    )


# -- pricing fallback ----------------------------------------------------------
# OpenRouter returns usage.cost on every response when usage.include is set. When it is
# missing, estimate from the public catalog (list prices per token), cached for a day.
_catalog: dict = {"at": 0.0, "prices": {}}
_catalog_lock = threading.Lock()


def _load_catalog() -> dict:
    with _catalog_lock:
        if time.time() - _catalog["at"] < 86_400 and _catalog["prices"]:
            return _catalog["prices"]
        try:
            data = requests.get(MODELS_URL, timeout=15).json().get("data", [])
            _catalog["prices"] = {m["id"]: (float(m["pricing"].get("prompt") or 0), float(m["pricing"].get("completion") or 0))
                                  for m in data if m.get("pricing")}
            _catalog["at"] = time.time()
        except Exception as e:  # noqa: BLE001 - pricing is best effort
            log.warning("OpenRouter catalog unavailable: %s", e)
        return _catalog["prices"]


def estimate_cost(model_id: str, tokens_in: int, tokens_out: int) -> float | None:
    p = _load_catalog().get(model_id)
    if not p:
        return None
    return round(tokens_in * p[0] + tokens_out * p[1], 6)
