"""Finnhub ticker validation for the add-to-watchlist flow.

Every call is logged to intel.api_usage so the quota ledger stays honest.
"""

from __future__ import annotations

import logging
import time

import requests

from . import db
from .config import FINNHUB_API_KEY, HTTP_TIMEOUT

log = logging.getLogger("stockintel.finnhub")

BASE = "https://finnhub.io/api/v1"


class ValidationUnavailable(Exception):
    """Finnhub is not configured or did not answer. Caller decides whether to proceed."""


def _log_usage(endpoint: str, ticker: str, status: str, http_code: int | None, ms: int) -> None:
    try:
        db.execute(
            "INSERT INTO api_usage (source, endpoint, ticker, status, http_code, ms) "
            "VALUES ('finnhub', %s, %s, %s, %s, %s)",
            (endpoint, ticker, status, http_code, ms),
        )
    except Exception as e:  # noqa: BLE001 - never fail the request over bookkeeping
        log.warning("api_usage insert failed: %s", e)


def validate(ticker: str) -> dict:
    """Return {ticker, name, cik, exchange} for a real symbol.

    Raises ValueError for an unknown symbol, ValidationUnavailable when Finnhub
    cannot be reached.
    """
    if not FINNHUB_API_KEY:
        raise ValidationUnavailable("FINNHUB_API_KEY not configured")

    sym = ticker.strip().upper()
    t0 = time.monotonic()
    try:
        resp = requests.get(
            f"{BASE}/stock/profile2",
            params={"symbol": sym, "token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        _log_usage("stock/profile2", sym, "error", None, int((time.monotonic() - t0) * 1000))
        raise ValidationUnavailable(str(e)) from e

    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code == 429:
        _log_usage("stock/profile2", sym, "quota_skip", 429, ms)
        raise ValidationUnavailable("Finnhub rate limit")
    if resp.status_code != 200:
        _log_usage("stock/profile2", sym, "error", resp.status_code, ms)
        raise ValidationUnavailable(f"Finnhub HTTP {resp.status_code}")

    _log_usage("stock/profile2", sym, "ok", 200, ms)
    body = resp.json() or {}

    # profile2 answers {} for an unknown symbol, and is thin for ETFs.
    if not body.get("name"):
        if _symbol_exists(sym):
            return {"ticker": sym, "name": sym, "cik": None, "exchange": None, "thin": True}
        raise ValueError(f"{sym} is not a known symbol on Finnhub")

    cik = (body.get("cik") or "").strip() or None
    return {
        "ticker": sym,
        "name": body.get("name"),
        "cik": cik,
        "exchange": body.get("exchange"),
        "thin": False,
    }


def _symbol_exists(sym: str) -> bool:
    """Second opinion via search. ETFs and newer listings miss profile2 but exist here."""
    t0 = time.monotonic()
    try:
        resp = requests.get(
            f"{BASE}/search",
            params={"q": sym, "exchange": "US", "token": FINNHUB_API_KEY},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException:
        _log_usage("search", sym, "error", None, int((time.monotonic() - t0) * 1000))
        return False
    ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        _log_usage("search", sym, "error", resp.status_code, ms)
        return False
    _log_usage("search", sym, "ok", 200, ms)
    results = (resp.json() or {}).get("result") or []
    return any((r.get("symbol") or "").upper() == sym for r in results)
