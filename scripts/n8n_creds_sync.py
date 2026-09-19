#!/usr/bin/env python3
"""Create the Stock Intel n8n credentials from the values in .env.

Reads: .env (API keys, DB password, n8n API key).
Writes: n8n credentials via POST /api/v1/credentials. Skips names already in the ledger.
Prints: credential name -> id only. Never prints a value.
Usage: python scripts/n8n_creds_sync.py [--dry-run]
"""
import json
import os
import sys
import urllib.error
import urllib.request

import secrets_env

N8N = secrets_env.get("N8N_BASE_URL", "http://localhost:5678").rstrip("/")
# Host as n8n resolves it, which is the compose service name, not the host loopback.
PG_HOST = secrets_env.get("N8N_PG_HOST", "postgres")
PG_PORT = int(secrets_env.get("N8N_PG_PORT", "5432"))
PG_DB = secrets_env.get("STOCKS_PG_DB", "stocks")
DRY = "--dry-run" in sys.argv


def n8n(method, path, key, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(N8N + path, data=data, method=method)
    req.add_header("X-N8N-API-KEY", key)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode()
            return r.status, (json.loads(t) if t else {})
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode()[:300]}


def main():
    key = secrets_env.require(os.environ.get("SI_N8N_KEY_NAME", "N8N_API_KEY"))
    g = secrets_env.get

    wanted = [
        ("Stock Intel Finnhub (query token)", "httpQueryAuth",
         {"name": "token", "value": secrets_env.require("FINNHUB_API_KEY")}),
        ("Stock Intel Alpha Vantage (query apikey)", "httpQueryAuth",
         {"name": "apikey", "value": secrets_env.require("ALPHA_VANTAGE_API_KEY")}),
        ("Stock Intel Alpaca (headers)", "httpCustomAuth",
         {"json": json.dumps({"headers": {
             "APCA-API-KEY-ID": secrets_env.require("ALPACA_API_KEY_ID"),
             "APCA-API-SECRET-KEY": secrets_env.require("ALPACA_API_SECRET_KEY")}})}),
    ]
    # Header auth for the app-driven webhooks on Daily and Backfill.
    hook = g("STOCK_INTEL_N8N_WEBHOOK_SECRET")
    if hook:
        wanted.append(("Stock Intel App Webhook (header)", "httpHeaderAuth",
                       {"name": "X-Stock-Intel-Secret", "value": hook}))

    bot = g("TELEGRAM_BOT_TOKEN")
    if bot:
        wanted.append(("Stock Intel Telegram", "telegramApi", {"accessToken": bot}))

    pg = g("STOCKS_PG_PASSWORD")
    if pg:
        wanted.append(("Stock Intel Postgres (stocks_n8n)", "postgres", {
            "host": PG_HOST, "port": PG_PORT, "database": PG_DB, "user": "stocks_n8n",
            "password": pg, "ssl": "disable", "allowUnauthorizedCerts": False}))

    # n8n public API has no credential list; use the MCP-visible names via a probe workflow list is overkill.
    # Instead keep a local ledger of created ids next to this script.
    ledger_path = os.path.join(os.path.dirname(__file__),
                               os.environ.get("SI_N8N_CRED_LEDGER", "n8n_creds.json"))
    ledger = json.load(open(ledger_path)) if os.path.exists(ledger_path) else {}

    for name, ctype, data in wanted:
        if name in ledger:
            print(f"exists  {name} -> {ledger[name]}")
            continue
        if DRY:
            print(f"would create {name} ({ctype})")
            continue
        code, body = n8n("POST", "/api/v1/credentials", key, {"name": name, "type": ctype, "data": data})
        if code not in (200, 201):
            print(f"FAILED  {name}: HTTP {code} {body.get('error', '')}")
            continue
        ledger[name] = body["id"]
        print(f"created {name} -> {body['id']}")

    if not DRY:
        json.dump(ledger, open(ledger_path, "w"), indent=2)


if __name__ == "__main__":
    main()
