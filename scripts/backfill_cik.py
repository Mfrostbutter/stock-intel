#!/usr/bin/env python3
"""Fill intel.watchlist.cik from SEC company_tickers.json. Idempotent; only touches rows with no cik.

Reads: STOCKS_PG_* and SEC_USER_AGENT from .env. ETFs and tickers SEC does not list stay null.
Usage: python scripts/backfill_cik.py
"""
import json
import urllib.request

import psycopg2

import secrets_env


def main():
    req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json",
                                 headers={"User-Agent": secrets_env.sec_user_agent()})
    with urllib.request.urlopen(req, timeout=30) as r:
        by_ticker = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in json.loads(r.read()).values()}
    conn = psycopg2.connect(**secrets_env.pg())
    with conn, conn.cursor() as cur:
        cur.execute("SELECT ticker FROM watchlist WHERE cik IS NULL ORDER BY 1")
        todo = [r[0] for r in cur.fetchall()]
        hit = [(by_ticker[t], t) for t in todo if t in by_ticker]
        cur.executemany("UPDATE watchlist SET cik = %s WHERE ticker = %s AND cik IS NULL", hit)
        print(f"filled {len(hit)} of {len(todo)}; no SEC match: {' '.join(t for t in todo if t not in by_ticker) or 'none'}")
    conn.close()


if __name__ == "__main__":
    main()
