"""Live-ish quotes from Alpaca. One HTTP call covers every symbol asked for.

Free tier is the IEX feed: real trades, but IEX only, so the last price can differ
slightly from the consolidated tape. Every call is logged to intel.api_usage.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import requests

from . import db
from .config import ALPACA_KEY_ID, ALPACA_SECRET_KEY, HTTP_TIMEOUT, QUOTE_CACHE_SECONDS, QUOTE_FEED

log = logging.getLogger("stockintel.alpaca")

BASE = "https://data.alpaca.markets/v2/stocks"
ET = ZoneInfo("America/New_York")
OPEN, CLOSE = dtime(9, 30), dtime(16, 0)

_lock = threading.Lock()
_cache: dict[str, object] = {"at": 0.0, "key": "", "quotes": {}}


class QuotesUnavailable(Exception):
    """Alpaca is not configured or did not answer."""


def market_open(now: datetime | None = None) -> bool:
    """Regular session only. Holidays are not modelled; a closed market just returns
    the previous close, which is what the UI would show anyway."""
    n = (now or datetime.now(ET)).astimezone(ET)
    return n.weekday() < 5 and OPEN <= n.time() <= CLOSE


def _log_usage(status: str, http_code: int | None, ms: int, n: int) -> None:
    try:
        db.execute(
            "INSERT INTO api_usage (source, endpoint, ticker, status, http_code, ms) "
            "VALUES ('alpaca', 'snapshots', %s, %s, %s, %s)",
            (f"{n} symbols", status, http_code, ms),
        )
    except Exception as e:  # noqa: BLE001 - bookkeeping never fails the request
        log.warning("api_usage insert failed: %s", e)


def _fetch(symbols: list[str]) -> dict:
    headers = {"APCA-API-KEY-ID": ALPACA_KEY_ID, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY}
    t0 = time.monotonic()
    try:
        resp = requests.get(
            f"{BASE}/snapshots",
            params={"symbols": ",".join(symbols), "feed": QUOTE_FEED},
            headers=headers,
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        _log_usage("error", None, int((time.monotonic() - t0) * 1000), len(symbols))
        raise QuotesUnavailable(str(e)) from e

    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code == 429:
        _log_usage("quota_skip", 429, ms, len(symbols))
        raise QuotesUnavailable("Alpaca rate limit")
    if resp.status_code != 200:
        _log_usage("error", resp.status_code, ms, len(symbols))
        raise QuotesUnavailable(f"Alpaca HTTP {resp.status_code}")

    _log_usage("ok", 200, ms, len(symbols))
    return resp.json() or {}


def _shape(sym: str, snap: dict) -> dict | None:
    trade = snap.get("latestTrade") or {}
    day = snap.get("dailyBar") or {}
    prev = snap.get("prevDailyBar") or {}
    price = trade.get("p") or day.get("c")
    base = prev.get("c")
    if price is None:
        return None
    change = (price - base) if base else None
    return {
        "ticker": sym,
        "price": price,
        "prev_close": base,
        "change": change,
        "change_pct": (change / base) if (change is not None and base) else None,
        "day_open": day.get("o"),
        "day_high": day.get("h"),
        "day_low": day.get("l"),
        "volume": day.get("v"),
        "at": trade.get("t") or day.get("t"),
    }


def quotes(symbols: list[str]) -> dict:
    """Cached snapshot for the symbols asked for. The cache is shared across callers
    so a second browser tab costs nothing."""
    if not (ALPACA_KEY_ID and ALPACA_SECRET_KEY):
        raise QuotesUnavailable("ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not configured")

    syms = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    if not syms:
        return {"quotes": {}, "fetched_at": None, "cached": False}

    key = ",".join(syms)
    with _lock:
        fresh = (time.monotonic() - float(_cache["at"])) < QUOTE_CACHE_SECONDS
        if fresh and _cache["key"] == key:
            return {"quotes": _cache["quotes"], "fetched_at": _cache["ts"], "cached": True}

    body = _fetch(syms)
    out = {}
    for sym in syms:
        shaped = _shape(sym, body.get(sym) or {})
        if shaped:
            out[sym] = shaped

    with _lock:
        _cache.update({"at": time.monotonic(), "key": key, "quotes": out,
                       "ts": datetime.now(ET).isoformat()})
    return {"quotes": out, "fetched_at": _cache["ts"], "cached": False}
