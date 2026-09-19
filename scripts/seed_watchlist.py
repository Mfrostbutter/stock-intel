#!/usr/bin/env python3
"""Seed intel.watchlist from watchlist.yaml. Re-runnable: adds and updates, never retires.

Each ticker is validated against Finnhub, its CIK filled from SEC company_tickers.json, and a
2-year price backfill fired for anything with no stored bars. The UI owns retirement, so a row
missing from the file is left alone unless --retire-missing says otherwise.

Usage: python scripts/seed_watchlist.py [--file watchlist.yaml] [--dry-run] [--retire-missing]
"""
import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request

import yaml

import secrets_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
FINNHUB = "https://finnhub.io/api/v1"
DRY = "--dry-run" in sys.argv
RETIRE = "--retire-missing" in sys.argv


def yaml_path():
    if "--file" in sys.argv:
        return pathlib.Path(sys.argv[sys.argv.index("--file") + 1])
    return ROOT / "watchlist.yaml"


def load_file(path):
    """Parse and check the file. Returns [{ticker, tags, notes, target_entry}] with benchmarks folded in."""
    if not path.is_file():
        raise SystemExit(f"{path} not found. Copy watchlist.example.yaml to watchlist.yaml first.")
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    benchmarks = [str(t).strip().upper() for t in (doc.get("benchmarks") or []) if str(t).strip()]
    if not benchmarks:
        raise SystemExit("watchlist.yaml needs at least one benchmark: scores are relative to it.")

    rows, seen = {}, set()
    for b in benchmarks:
        rows[b] = {"ticker": b, "tags": ["benchmark"], "notes": None, "target_entry": None}
    for raw in doc.get("tickers") or []:
        if isinstance(raw, str):
            raw = {"ticker": raw}
        if not isinstance(raw, dict) or not raw.get("ticker"):
            raise SystemExit(f"bad entry in {path.name}: {raw!r}")
        sym = str(raw["ticker"]).strip().upper()
        if sym in seen:
            raise SystemExit(f"{sym} is listed twice")
        seen.add(sym)
        tags = [str(t).strip() for t in (raw.get("tags") or []) if str(t).strip()]
        if "holding" in tags:
            raise SystemExit(f"{sym}: the 'holding' tag is derived from your Positions, remove it from the file")
        if sym in rows:                       # also named as a benchmark
            tags = sorted(set(tags) | {"benchmark"})
        target = raw.get("target_entry")
        rows[sym] = {"ticker": sym, "tags": tags,
                     "notes": (str(raw["notes"]) if raw.get("notes") else None),
                     "target_entry": (float(target) if target is not None else None)}
    return list(rows.values())


def get_json(url, headers=None, timeout=30):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def finnhub_name(sym, key):
    """Company name for a real symbol, or None when Finnhub does not know it."""
    q = urllib.parse.urlencode({"symbol": sym, "token": key})
    try:
        body = get_json(f"{FINNHUB}/stock/profile2?{q}")
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise SystemExit("Finnhub rate limit; wait a minute and re-run") from e
        raise
    return (body or {}).get("name") or None


def sec_ciks():
    try:
        data = get_json("https://www.sec.gov/files/company_tickers.json",
                        {"User-Agent": secrets_env.sec_user_agent()})
    except Exception as e:  # SEC is a nice-to-have; ETFs have no CIK anyway
        print(f"SEC CIK lookup skipped: {e}")
        return {}
    return {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}


def main():
    import psycopg2   # imported here so the loader can be tested without a driver

    rows = load_file(yaml_path())
    key = secrets_env.require("FINNHUB_API_KEY")
    ciks = sec_ciks()

    conn = psycopg2.connect(**secrets_env.pg())
    conn.autocommit = True
    added, updated, skipped, backfill = [], [], [], []
    with conn.cursor() as cur:
        cur.execute("SELECT ticker FROM watchlist")
        existing = {r[0] for r in cur.fetchall()}

        for row in rows:
            sym = row["ticker"]
            name = finnhub_name(sym, key)
            if not name:
                # ETFs have no profile2 entry; take them at face value, warn on the rest.
                print(f"  {sym}: no Finnhub profile (ETF or unknown symbol), added without a name")
            if DRY:
                print(f"would upsert {sym} tags={row['tags']}")
                continue
            cur.execute(
                "INSERT INTO watchlist (ticker, name, tags, cik, active, target_entry, notes, added_by) "
                "VALUES (%s, %s, %s, %s, true, %s, %s, 'seed') "
                "ON CONFLICT (ticker) DO UPDATE SET "
                "  name = COALESCE(EXCLUDED.name, watchlist.name), "
                "  tags = (SELECT array_agg(DISTINCT t) FROM unnest("
                "            EXCLUDED.tags || CASE WHEN 'holding' = ANY(watchlist.tags) "
                "                                  THEN ARRAY['holding'] ELSE ARRAY[]::text[] END) t), "
                "  cik = COALESCE(EXCLUDED.cik, watchlist.cik), "
                "  active = true, retired_at = NULL, "
                "  target_entry = COALESCE(EXCLUDED.target_entry, watchlist.target_entry), "
                "  notes = COALESCE(EXCLUDED.notes, watchlist.notes)",
                (sym, name, row["tags"], ciks.get(sym), row["target_entry"], row["notes"]))
            (updated if sym in existing else added).append(sym)

            cur.execute("SELECT count(*) FROM prices_daily WHERE ticker = %s", (sym,))
            if cur.fetchone()[0] == 0:
                backfill.append(sym)

        if RETIRE and not DRY:
            keep = [r["ticker"] for r in rows]
            cur.execute("UPDATE watchlist SET active = false, retired_at = now() "
                        "WHERE active AND NOT (ticker = ANY(%s)) "
                        "AND ticker NOT IN (SELECT ticker FROM positions WHERE qty > 0) "
                        "RETURNING ticker", (keep,))
            skipped = [r[0] for r in cur.fetchall()]
    conn.close()

    print(f"added {len(added)}, updated {len(updated)}" + (f", retired {len(skipped)}" if RETIRE else ""))
    if backfill and not DRY:
        fire_backfill(backfill)


def fire_backfill(tickers):
    """Ask n8n for 2 years of prices. Harmless to skip: the next daily run picks them up."""
    secret = secrets_env.get("STOCK_INTEL_N8N_WEBHOOK_SECRET")
    base = secrets_env.get("N8N_WEBHOOK_URL", "http://localhost:5678/").rstrip("/")
    path = secrets_env.get("N8N_BACKFILL_WEBHOOK_PATH", "stock-intel/backfill").strip("/")
    if not secret:
        print(f"no webhook secret set; backfill not fired for {len(tickers)} ticker(s)")
        return
    body = json.dumps({"tickers": tickers, "source": "seed"}).encode()
    req = urllib.request.Request(f"{base}/webhook/{path}", data=body, method="POST",
                                 headers={"Content-Type": "application/json",
                                          "X-Stock-Intel-Secret": secret})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            print(f"backfill fired for {len(tickers)} ticker(s): HTTP {r.status}")
    except Exception as e:
        print(f"backfill webhook failed ({e}); run it later from the app's Watchlist screen")


if __name__ == "__main__":
    main()
