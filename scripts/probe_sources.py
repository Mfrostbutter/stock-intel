#!/usr/bin/env python3
"""Smoke-test each data source with the keys in .env. Prints status + one datum, never a key.

Usage: python scripts/probe_sources.py [TICKER]
"""
import datetime as dt
import json
import sys
import urllib.error
import urllib.request

import secrets_env

UA = {"User-Agent": secrets_env.get("SEC_USER_AGENT", "stock-intel (set SEC_USER_AGENT)")}
S = (sys.argv[1] if len(sys.argv) > 1 else "NVDA").upper()
TODAY = dt.date.today()
WEEK_AGO = TODAY - dt.timedelta(days=7)
MONTH_AGO = TODAY - dt.timedelta(days=30)
TWO_YEARS_AGO = TODAY - dt.timedelta(days=730)


def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or UA)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]
    except Exception as e:
        return -1, str(e)[:200]


k = secrets_env.get("FINNHUB_API_KEY")
if not k:
    print("finnhub SKIP (FINNHUB_API_KEY not set)")
else:
    c, b = get(f"https://finnhub.io/api/v1/quote?symbol={S}&token={k}")
    print("finnhub quote", c, b.get("c") if isinstance(b, dict) else b)
    c, b = get(f"https://finnhub.io/api/v1/company-news?symbol={S}&from={WEEK_AGO}&to={TODAY}&token={k}")
    print("finnhub news", c, len(b) if isinstance(b, list) else b)
    c, b = get(f"https://finnhub.io/api/v1/stock/recommendation?symbol={S}&token={k}")
    print("finnhub recs", c, (b[0].get("period") if isinstance(b, list) and b else b))
    c, b = get(f"https://finnhub.io/api/v1/stock/insider-transactions?symbol={S}&token={k}")
    print("finnhub insider", c, len(b.get("data", [])) if isinstance(b, dict) else b)
    c, b = get(f"https://finnhub.io/api/v1/stock/metric?symbol={S}&metric=all&token={k}")
    print("finnhub metric", c, (len(b.get("metric", {})) if isinstance(b, dict) else b))
    c, b = get(f"https://finnhub.io/api/v1/calendar/earnings?from={TODAY}&to={TODAY + dt.timedelta(days=14)}&token={k}")
    print("finnhub earnings cal", c, (len(b.get("earningsCalendar", [])) if isinstance(b, dict) else b))

kid, ks = secrets_env.get("ALPACA_API_KEY_ID"), secrets_env.get("ALPACA_API_SECRET_KEY")
if not (kid and ks):
    print("alpaca SKIP (ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY not set)")
else:
    h = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": ks}
    c, b = get(f"https://data.alpaca.markets/v2/stocks/bars?symbols={S},SPY,QQQ&timeframe=1Day&start={MONTH_AGO}&limit=10&feed=iex", h)
    print("alpaca bars iex", c, {s: len(v) for s, v in b.get("bars", {}).items()} if isinstance(b, dict) else b)
    # sip is the consolidated tape; a free plan returns 403 or refuses the last 15 minutes.
    c, b = get(f"https://data.alpaca.markets/v2/stocks/bars?symbols={S}&timeframe=1Day&start={MONTH_AGO}&limit=10&feed=sip", h)
    print("alpaca bars sip", c, (len(b.get("bars", {}).get(S, [])) if isinstance(b, dict) else b))
    c, b = get(f"https://data.alpaca.markets/v2/stocks/bars?symbols={S}&timeframe=1Day&start={TWO_YEARS_AGO}&limit=10000&feed=iex", h)
    print("alpaca 2y backfill", c, (len(b.get("bars", {}).get(S, [])) if isinstance(b, dict) else b))

k = secrets_env.get("ALPHA_VANTAGE_API_KEY")
if not k:
    print("alphavantage SKIP (ALPHA_VANTAGE_API_KEY not set)")
else:
    c, b = get(f"https://www.alphavantage.co/query?function=NEWS_SENTIMENT&topics=technology&limit=5&apikey={k}")
    print("alphavantage news", c, (b.get("items") or b.get("Information") or b.get("Note") or list(b.keys())[:3]) if isinstance(b, dict) else b)

c, b = get("https://www.sec.gov/files/company_tickers.json")
print("sec tickers map", c, (len(b) if isinstance(b, dict) else b))
