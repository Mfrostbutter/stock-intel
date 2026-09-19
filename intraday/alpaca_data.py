"""Read-only Alpaca market data + clock via REST (stdlib). Engineering mode = IEX feed.

Websocket streaming (alpaca-py StockDataStream) is the next iteration; this REST poll of
completed 1-min bars is the skeleton and doubles as the gap-repair/warm-up path.
"""
import functools
import json
import urllib.error
import urllib.request

from . import config


@functools.lru_cache(maxsize=1)
def _auth():
    return {"APCA-API-KEY-ID": config.secret("ALPACA_API_KEY_ID"),
            "APCA-API-SECRET-KEY": config.secret("ALPACA_API_SECRET_KEY")}


def _get(url):
    req = urllib.request.Request(url, headers=_auth())
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def clock():
    return _get(f"{config.ALPACA_PAPER}/v2/clock")


def bars_1min(symbols, start_iso, limit=1000):
    """Completed 1-min bars per symbol since start_iso (RFC3339). Follows pagination."""
    syms = ",".join(symbols)
    out = {}
    page = None
    while True:
        url = (f"{config.ALPACA_DATA}/v2/stocks/bars?symbols={syms}&timeframe=1Min"
               f"&start={start_iso}&limit={limit}&feed={config.FEED}&adjustment=raw&sort=asc")
        if page:
            url += f"&page_token={page}"
        d = _get(url)
        for sym, arr in (d.get("bars") or {}).items():
            out.setdefault(sym, []).extend(arr)
        page = d.get("next_page_token")
        if not page:
            return out


def latest_quotes(symbols):
    url = f"{config.ALPACA_DATA}/v2/stocks/quotes/latest?symbols={','.join(symbols)}&feed={config.FEED}"
    return _get(url).get("quotes") or {}
