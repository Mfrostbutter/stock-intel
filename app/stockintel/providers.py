"""Model providers: registry in intel.config, live model catalogs, chat clients per provider.

A model reference is "<provider_id>:<model_id>"; a bare model id means the default provider
(openrouter). Keys never live in the database: each provider names the environment variable
that carries its key, and the UI only learns whether that variable is set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any

import requests

from . import db
from .analyst import openrouter
from .config import OLLAMA_BASE_URL, OPENAI_BASE_URL

log = logging.getLogger("stockintel.providers")

CONFIG_KEY = "analyst_providers"
DEFAULT_PROVIDER = "openrouter"
CATALOG_TTL = 300.0
HTTP_TIMEOUT = 20

# A local model host needs no key; "ollama" is the one kind that works with an empty key_env.
KINDS = {
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "key_env": "OPENROUTER_API_KEY"},
    "anthropic": {"base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY"},
    "openai": {"base_url": OPENAI_BASE_URL or "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY"},
    "ollama": {"base_url": f"{OLLAMA_BASE_URL}/v1", "key_env": "OLLAMA_API_KEY"},
}
KEYLESS_KINDS = {"ollama"}

# Either spelling of a renamed key variable resolves, so an older provider row keeps working.
KEY_ALIASES = {"OPENROUTER_API_KEY": "OPEN_ROUTER_API_KEY",
               "OPEN_ROUTER_API_KEY": "OPENROUTER_API_KEY",
               "ANTHROPIC_API_KEY": "STOCK_INTEL_ANTHROPIC_KEY",
               "STOCK_INTEL_ANTHROPIC_KEY": "ANTHROPIC_API_KEY"}

DEFAULTS = [
    {"id": "openrouter", "name": "OpenRouter", "kind": "openrouter", "base_url": KINDS["openrouter"]["base_url"],
     "key_env": "OPENROUTER_API_KEY", "enabled": True},
]

_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
_ENV_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")

_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()


# -- registry ---------------------------------------------------------------------

def registry() -> list[dict]:
    row = db.one("SELECT value FROM config WHERE key = %s", (CONFIG_KEY,))
    items = row["value"] if row and isinstance(row["value"], list) else DEFAULTS
    return [dict(p) for p in items] or [dict(p) for p in DEFAULTS]


def with_status(items: list[dict]) -> list[dict]:
    out = []
    for p in items:
        q = dict(p)
        q["key_present"] = bool(key_for(p))
        out.append(q)
    return out


def _private_http(base: str) -> bool:
    """Plain http is allowed only for a local model host: loopback, RFC1918, or a container name."""
    if not base.startswith("http://"):
        return False
    host = base[len("http://"):].split("/")[0].split(":")[0].lower()
    if host in ("localhost", "127.0.0.1", "::1", "host.docker.internal"):
        return True
    if "." not in host:  # compose service name, e.g. http://ollama:11434
        return True
    parts = host.split(".")
    if not all(p.isdigit() for p in parts) or len(parts) != 4:
        return False
    a, b = int(parts[0]), int(parts[1])
    return a == 10 or a == 127 or (a == 192 and b == 168) or (a == 172 and 16 <= b <= 31)


def validate(items: list[Any]) -> list[dict]:
    """Normalise a provider list from the UI. Raises ValueError with a message the UI can show."""
    if not isinstance(items, list) or not items:
        raise ValueError("at least one provider is required")
    seen: set[str] = set()
    out: list[dict] = []
    for raw in items:
        if not isinstance(raw, dict):
            raise ValueError("each provider must be an object")
        pid = str(raw.get("id") or "").strip().lower()
        kind = str(raw.get("kind") or "").strip().lower()
        if not _ID_RE.match(pid):
            raise ValueError(f"provider id '{pid}' must be 2-32 chars: a-z, 0-9, _ or -")
        if pid in seen:
            raise ValueError(f"duplicate provider id '{pid}'")
        if kind not in KINDS:
            raise ValueError(f"provider '{pid}': kind must be one of {', '.join(KINDS)}")
        base = str(raw.get("base_url") or KINDS[kind]["base_url"]).strip().rstrip("/")
        if not base.startswith("https://") and not _private_http(base):
            raise ValueError(f"provider '{pid}': base_url must be https (or http on a local address)")
        env = str(raw.get("key_env") or KINDS[kind]["key_env"]).strip()
        if not _ENV_RE.match(env):
            raise ValueError(f"provider '{pid}': key_env must be an UPPER_SNAKE environment variable name")
        for k in ("api_key", "key", "secret", "token"):
            if raw.get(k):
                raise ValueError("keys are not stored here; put the value in .env and name its variable in key_env")
        seen.add(pid)
        out.append({"id": pid, "name": str(raw.get("name") or pid).strip()[:60], "kind": kind,
                    "base_url": base, "key_env": env, "enabled": bool(raw.get("enabled", True))})
    if not any(p["enabled"] for p in out):
        raise ValueError("at least one provider must be enabled")
    return out


def save(items: list[Any]) -> list[dict]:
    clean = validate(items)
    db.execute("INSERT INTO config (key, value, updated_at) VALUES (%s, %s::jsonb, now()) "
               "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
               (CONFIG_KEY, json.dumps(clean)))
    return with_status(clean)


def get(provider_id: str) -> dict:
    for p in registry():
        if p["id"] == provider_id:
            return p
    raise KeyError(f"unknown provider '{provider_id}'")


def key_for(p: dict) -> str:
    name = p.get("key_env") or ""
    key = os.environ.get(name, "").strip() or os.environ.get(KEY_ALIASES.get(name, ""), "").strip()
    # Ollama ignores the bearer token but the OpenAI client insists on one.
    return key or ("ollama" if p.get("kind") in KEYLESS_KINDS else "")


# -- model references ------------------------------------------------------------

def parse_ref(ref: str) -> tuple[str, str]:
    """'openrouter:anthropic/claude-sonnet-5' -> ('openrouter', 'anthropic/claude-sonnet-5').
    A bare id belongs to the default provider; 'https://' never appears so ':' is unambiguous."""
    ref = (ref or "").strip()
    if ":" in ref and not ref.startswith(("http:", "https:")):
        pid, mid = ref.split(":", 1)
        if _ID_RE.match(pid) and mid:
            return pid, mid
    return DEFAULT_PROVIDER, ref


EFFORTS = ("low", "medium", "high")


def chat_model_for(ref: str, temperature: float = 0.2, reasoning_effort: str | None = None,
                   provider_prefs: dict | None = None):
    """Build the LangChain chat model for a reference. Raises with a plain message when the key is missing.
    reasoning_effort (low, medium, high) is sent through OpenRouter's reasoning field; other kinds ignore it."""
    pid, mid = parse_ref(ref)
    p = get(pid)
    if not p.get("enabled", True):
        raise RuntimeError(f"provider '{pid}' is disabled")
    key = key_for(p)
    if not key:
        raise RuntimeError(f"provider '{pid}': environment variable {p['key_env']} is empty")
    if p["kind"] == "ollama":
        # OpenAI-compatible endpoint, no cost, and slower: the critique step still checks the work.
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=mid, api_key=key, base_url=p["base_url"], temperature=temperature,
                          max_tokens=openrouter.DEFAULT_MAX_TOKENS, max_retries=1, timeout=600)
    if p["kind"] == "openrouter":
        if "/" not in mid and pid == DEFAULT_PROVIDER:
            mid = f"anthropic/{mid}"   # bare Claude id from the 002 seed
        extra: dict = {}
        if reasoning_effort in EFFORTS:
            extra["reasoning"] = {"effort": reasoning_effort}
        if provider_prefs:
            extra["provider"] = dict(provider_prefs)   # routing prefs (sort, order); deny is added by the client
        return openrouter.chat_model(mid, key, temperature=temperature, extra=extra or None)
    if p["kind"] == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=mid, api_key=key, temperature=temperature, max_tokens=openrouter.DEFAULT_MAX_TOKENS,
                             base_url=p["base_url"].removesuffix("/v1"))
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model=mid, api_key=key, base_url=p["base_url"], temperature=temperature,
                      max_tokens=openrouter.DEFAULT_MAX_TOKENS, max_retries=2)


# -- catalogs --------------------------------------------------------------------

def _norm_price(v: Any) -> float | None:
    try:
        f = float(v)
        return round(f * 1_000_000, 4) if f >= 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_openrouter(p: dict, key: str) -> list[dict]:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = requests.get(f"{p['base_url']}/models", headers=headers, timeout=HTTP_TIMEOUT).json().get("data", [])
    out = []
    for m in data:
        sp = set(m.get("supported_parameters") or [])
        pr = m.get("pricing") or {}
        out.append({"id": m["id"], "name": m.get("name") or m["id"], "context": m.get("context_length"),
                    "price_in": _norm_price(pr.get("prompt")), "price_out": _norm_price(pr.get("completion")),
                    "tools": "tools" in sp, "structured": bool(sp & {"structured_outputs", "response_format"}),
                    "reasoning": "reasoning" in sp or "include_reasoning" in sp})
    return out


def _fetch_anthropic(p: dict, key: str) -> list[dict]:
    if not key:
        raise RuntimeError("key missing")
    r = requests.get(f"{p['base_url']}/models", headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
                     params={"limit": 100}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return [{"id": m["id"], "name": m.get("display_name") or m["id"], "context": None, "price_in": None,
             "price_out": None, "tools": True, "structured": True, "reasoning": None} for m in r.json().get("data", [])]


def _fetch_ollama(p: dict, key: str) -> list[dict]:
    """Local model list. Free by definition, so prices are 0 rather than unknown."""
    r = requests.get(f"{p['base_url']}/models", timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return sorted(({"id": m["id"], "name": m["id"], "context": None, "price_in": 0.0, "price_out": 0.0,
                    "tools": None, "structured": None, "reasoning": None}
                   for m in r.json().get("data", [])), key=lambda x: x["id"])


def _fetch_openai(p: dict, key: str) -> list[dict]:
    if not key:
        raise RuntimeError("key missing")
    r = requests.get(f"{p['base_url']}/models", headers={"Authorization": f"Bearer {key}"}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for m in r.json().get("data", []):
        mid = m["id"]
        if any(x in mid for x in ("embedding", "whisper", "tts", "dall-e", "moderation", "realtime", "transcribe", "image")):
            continue
        out.append({"id": mid, "name": mid, "context": None, "price_in": None, "price_out": None,
                    "tools": None, "structured": None, "reasoning": None})
    return sorted(out, key=lambda x: x["id"])


_FETCH = {"openrouter": _fetch_openrouter, "anthropic": _fetch_anthropic, "openai": _fetch_openai,
          "ollama": _fetch_ollama}


def _configured_ids() -> list[str]:
    rows = db.rows("SELECT value FROM config WHERE key IN ('analyst_model','analyst_cheap_model','analyst_deep_model','analyst_bakeoff_models')")
    ids: list[str] = []
    for r in rows:
        v = r["value"]
        ids.extend(v if isinstance(v, list) else [v])
    return [str(x) for x in ids if x]


def list_models(provider_id: str, force: bool = False) -> dict:
    """Catalog for one provider: live, cached (5 min) or fallback to the ids already configured."""
    p = get(provider_id)
    key = key_for(p)
    fp = str(hash(key))[-6:]
    with _cache_lock:
        c = _cache.get(provider_id)
        if c and not force and c["fp"] == fp and time.time() - c["at"] < CATALOG_TTL:
            return {"provider": provider_id, "models": c["models"], "source": "cached", "cached_at": c["at"],
                    "key_present": bool(key)}
    try:
        models = _FETCH[p["kind"]](p, key)
        with _cache_lock:
            _cache[provider_id] = {"at": time.time(), "models": models, "fp": fp}
        return {"provider": provider_id, "models": models, "source": "live", "cached_at": time.time(),
                "key_present": bool(key)}
    except Exception as e:  # noqa: BLE001 - the dropdown must still show something
        log.warning("model catalog for %s unavailable: %s", provider_id, e)
        fallback = [{"id": parse_ref(x)[1], "name": parse_ref(x)[1], "context": None, "price_in": None,
                     "price_out": None, "tools": None, "structured": None, "reasoning": None}
                    for x in _configured_ids() if parse_ref(x)[0] == provider_id]
        return {"provider": provider_id, "models": fallback, "source": "fallback", "cached_at": None,
                "key_present": bool(key), "error": f"{type(e).__name__}: {str(e)[:160]}"}


def test(provider_id: str) -> dict:
    """Reachability plus key check. OpenRouter also reports the key's limit and usage when it has one."""
    p = get(provider_id)
    key = key_for(p)
    t0 = time.time()
    try:
        models = _FETCH[p["kind"]](p, key)
        out = {"ok": True, "models": len(models), "ms": int((time.time() - t0) * 1000), "key_present": bool(key)}
        if p["kind"] == "openrouter" and key:
            r = requests.get(f"{p['base_url']}/key", headers={"Authorization": f"Bearer {key}"}, timeout=HTTP_TIMEOUT)
            if r.ok:
                d = r.json().get("data", {})
                out["key"] = {"label": d.get("label"), "usage": d.get("usage"), "limit": d.get("limit"),
                              "limit_remaining": d.get("limit_remaining"), "is_free_tier": d.get("is_free_tier")}
            else:
                out["key_error"] = f"HTTP {r.status_code}"
        return out
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}", "key_present": bool(key)}
