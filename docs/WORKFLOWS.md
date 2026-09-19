# Workflows

Twelve n8n workflows: nine for the daily pipeline, three optional intraday ones. They live as JSON
in `workflows/`, carry pinned ids from `deploy/n8n/ids.json`, and are imported and activated by
`scripts/bootstrap.py`. Editing them is a repo operation, not a UI one.

## Catalog

| Workflow | Nodes | Trigger | Writes | Calls |
|---|---|---|---|---|
| **Daily** | 27 | cron `0 6 * * 1-5`, or `POST /webhook/stock-intel/run` | `runs`, `scores_daily`, `reports`, `api_usage` | the six collectors, then the app's analyze endpoint |
| **Backfill** | 8 | manual, or `POST /webhook/stock-intel/backfill {"tickers":[...]}` | `prices_daily`, `indicators_daily` | Collect-Prices, Compute-Indicators |
| Collect-Prices | 7 | sub-workflow | `prices_daily`, `api_usage` | Alpaca `/v2/stocks/bars` |
| Compute-Indicators | 5 | sub-workflow | `indicators_daily` | none, pure JS |
| Collect-News | 12 | sub-workflow | `news_articles`, `sentiment_daily`, `api_usage` | Finnhub, Alpha Vantage |
| Collect-Earnings | 6 | sub-workflow | `earnings_calendar` | Finnhub |
| Collect-Fundamentals | 6 | sub-workflow | `fundamentals`, `api_usage` | Finnhub |
| Collect-Signals | 7 | sub-workflow | `insider_tx`, `analyst_recs`, `api_usage` | Finnhub |
| **Entry Signals** | 6 | cron 10:00, 12:00, 14:00, 15:45 weekdays | via the app: `entry_signals` | Alpaca clock, app `/api/entries/signal` |
| Intraday Session Prep (WF-01) | 12 | 06:00 and every 5 min 08:00 to 09:55 | scan snapshots, calendar, session manifest | Alpaca |
| Intraday Health (WF-07) | 7 | every minute, plus an error trigger | `intraday.incidents` | none |
| Intraday Gap Repair (WF-08) | 8 | every 5 minutes | `intraday.bar_versions` | Alpaca |

The three intraday workflows import inactive. `scripts/bootstrap.py --intraday` activates them
together with the recorder profile.

## How the daily run hangs together

```
cron / webhook
  -> Start run + watchlist      one SQL: opens a run row, returns tickers, benchmarks, holdings,
                                weights, positions, quota state, app_base_url, telegram settings
  -> Alpaca calendar            last 14 sessions
  -> Build context              as_of = last completed session, so a holiday is never scored
  -> six collectors in sequence, each handed the same context item
  -> Load snapshot              one SQL joins everything collected into one row per ticker
  -> Score v2                   the arithmetic, in JS, no model involved
  -> Upsert scores_daily
  -> Build brief                markdown + HTML + the short Telegram message + run status
  -> Telegram (optional)        message, then the full brief as a .md attachment
  -> Store report + finish run
  -> Analyze brief              POST to the app; the analyst runs in the background
```

The context every collector receives is `{run_id, as_of, tickers[], benchmarks[], start, end,
days_back}`. Every collector answers with one counts item: `{source, rows, calls, failed,
failed_tickers, ok}`. If any `ok` is false the run is marked `degraded`, the issues are listed in
`runs.summary` and printed in the brief footer, and the run still finishes. A collector that dies
degrades the brief; it never kills the pipeline.

## Credentials

Six, created from `.env` by bootstrap and bound by both id and name:

| Credential | Type | Used by |
|---|---|---|
| `Stock Intel Postgres (stocks_n8n)` | postgres | every workflow that touches the database |
| `Stock Intel Alpaca (headers)` | custom auth | price and clock calls |
| `Stock Intel Finnhub (query token)` | query auth | news, earnings, fundamentals, signals |
| `Stock Intel Alpha Vantage (query apikey)` | query auth | topic sentiment |
| `Stock Intel App Webhook (header)` | header auth | the app calling n8n, and n8n calling the app |
| `Stock Intel Telegram` | telegram | the brief, when a chat id is set |

## Two standards the deploy script enforces

`scripts/deploy_workflow.py` refuses to deploy a workflow that breaks either one:

- **Every workflow is tagged.** `stock-intel` plus `daily` or `intraday`, and `subworkflow` for the
  callable ones. Tags are how an n8n instance stays sortable once it holds more than a handful.
- **Every canvas is documented.** Background stickies are generated from a zone spec in
  `workflows/zones/`, and `scripts/canvas/layout_check.py` must print `RESULT: ok`. See
  [CANVAS.md](CANVAS.md).

## Editing a workflow

```bash
# 1. edit workflows/<name>.json
# 2. keep workflows/zones/<name>.json in step if you added or removed a node
python scripts/canvas/zone_layout.py workflows/daily.json workflows/zones/daily.json
python scripts/canvas/layout_check.py workflows/daily.json
python scripts/deploy_workflow.py workflows/daily.json --activate
```

`deploy_workflow.py` needs `N8N_BASE_URL` and `N8N_API_KEY` in `.env`. It activates before it
updates, then verifies that the published version is the one it just sent: activating afterwards
republishes whatever was active before and silently discards the change, which is a very quiet way
to lose an afternoon.

You can of course edit in the n8n UI to try something. Export the JSON back into the repo when it
works, or the next `bootstrap.py` will overwrite it.

## Adding a collector

1. Copy the closest existing collector; they are all the same shape (When called, fetch, flatten,
   upsert, count).
2. Give it an id in `deploy/n8n/ids.json` and a zone spec.
3. Add an Execute Workflow node to Daily pointing at the pinned id, plus a `Ctx` node after it to
   re-emit the context (a sub-workflow returns its own counts, not the input).
4. Fold its counts into the Build brief status check so a failure degrades the run.
