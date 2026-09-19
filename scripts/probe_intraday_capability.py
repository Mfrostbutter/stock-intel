#!/usr/bin/env python3
"""Capability audit for the intraday fork. Read-only: NO orders, NO account changes.
Probes the Alpaca PAPER account, feed entitlements, asset order-metadata, and the session
calendar with the keys in .env. Prints status + one datum per check, never a key or account
number. Output feeds docs/INTRADAY.md.

Usage: python scripts/probe_intraday_capability.py [TICKER ...]
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.request

import secrets_env

PAPER = "https://paper-api.alpaca.markets"
DATA = "https://data.alpaca.markets"
SYMBOLS = [a.upper() for a in sys.argv[1:]] or ["NVDA", "SPY", "QQQ"]


def get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        return e.code, body
    except Exception as e:
        return -1, str(e)[:200]


KID, KS = secrets_env.require("ALPACA_API_KEY_ID"), secrets_env.require("ALPACA_API_SECRET_KEY")
H = {"APCA-API-KEY-ID": KID, "APCA-API-SECRET-KEY": KS}
print("keys present:", bool(KID) and bool(KS))

# 1. Paper trading account. The single most important check: can these keys trade paper?
c, b = get(f"{PAPER}/v2/account", H)
if isinstance(b, dict):
    print("paper /account", c, {k: b.get(k) for k in (
        "status", "currency", "buying_power", "cash", "pattern_day_trader",
        "trading_blocked", "account_blocked", "shorting_enabled", "multiplier", "crypto_status")})
else:
    print("paper /account", c, b)

# 2. Account configurations (fractional trading toggle, etc.)
c, b = get(f"{PAPER}/v2/account/configurations", H)
print("paper /account/configurations", c, b if isinstance(b, dict) else b)

# 3. Clock: session state + boundaries + timezone anchor.
c, b = get(f"{PAPER}/v2/clock", H)
print("paper /clock", c, b if isinstance(b, dict) else b)

# 4. Calendar range (session boundaries, early closes) for the next ~2 weeks.
c, b = get(f"{PAPER}/v2/calendar?start={dt.date.today()}&end={dt.date.today() + dt.timedelta(days=60)}", H)
if isinstance(b, list):
    early = [d for d in b if d.get("close") and d["close"] != "16:00"]
    print("paper /calendar", c, "sessions=", len(b), "early_closes=", [(d.get("date"), d.get("close")) for d in early[:5]])
else:
    print("paper /calendar", c, b)

# 5. Asset order-metadata for candidates (fractionable/tradable/marginable). No orders placed.
for sym in SYMBOLS:
    c, b = get(f"{PAPER}/v2/assets/{sym}", H)
    if isinstance(b, dict):
        print(f"asset {sym}", c, {k: b.get(k) for k in (
            "status", "tradable", "fractionable", "marginable", "shortable",
            "easy_to_borrow", "min_order_size", "min_trade_increment", "price_increment")})
    else:
        print(f"asset {sym}", c, b)

# 6. Feed entitlement: IEX (free Basic) vs SIP (paid). Expect iex 200, sip 403 on Basic.
for feed in ("iex", "sip"):
    c, b = get(f"{DATA}/v2/stocks/{SYMBOLS[0]}/quotes/latest?feed={feed}", H)
    q = b.get("quote", {}) if isinstance(b, dict) else b
    print(f"latest quote feed={feed}", c, {k: q.get(k) for k in ("t", "ap", "bp")} if isinstance(q, dict) else q)

# 7. Minute bars (IEX): access + recency/delay.
c, b = get(f"{DATA}/v2/stocks/bars?symbols={SYMBOLS[0]}&timeframe=1Min&limit=3&feed=iex", H)
bars = (b.get("bars", {}) or {}).get(SYMBOLS[0], []) if isinstance(b, dict) else b
print("1Min bars iex", c, "n=", len(bars) if isinstance(bars, list) else bars,
      "last_t=", bars[-1].get("t") if isinstance(bars, list) and bars else None)

# 8. News entitlement + timestamps.
c, b = get(f"{DATA}/v1beta1/news?symbols={SYMBOLS[0]}&limit=2", H)
news = b.get("news", []) if isinstance(b, dict) else b
print("news", c, "n=", len(news) if isinstance(news, list) else news,
      "last=", (news[0].get("updated_at") if isinstance(news, list) and news else None))
