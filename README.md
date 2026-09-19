# Stock Intel

A self-hosted daily research pipeline for a watchlist you choose. It collects prices, news,
sentiment, earnings, fundamentals and insider and analyst signals every weekday morning, scores
each ticker with rules you can read and change, writes a brief you read in the browser, and
optionally lets an LLM analyst work over the same data with citations.

Everything runs on your own machine: Postgres, n8n and a FastAPI app in three containers. The data
providers all have free tiers. **Framework, not advice; nothing here places orders.** See
[DISCLAIMER.md](DISCLAIMER.md).

## Quickstart

```bash
git clone https://github.com/Mfrostbutter/stock-intel && cd stock-intel

cp .env.example .env
python scripts/bootstrap.py --generate-secrets     # fills every blank password and token
# put your API keys in .env: Alpaca, Finnhub, Alpha Vantage, and one LLM provider
# (docs/SETUP-APIS.md walks through each one, all free tiers)

cp watchlist.example.yaml watchlist.yaml           # edit tickers, tags, benchmarks
docker compose up -d                               # postgres + n8n + app

# open http://localhost:5678 once: create the n8n owner account, then
# Settings -> n8n API -> create an API key and put it in .env as N8N_API_KEY

python scripts/bootstrap.py                        # migrations, watchlist, credentials,
                                                   # workflows, activation, price backfill
open http://localhost:8095/ui                      # paste STOCK_INTEL_APP_TOKEN once
```

About fifteen minutes from clone to a scored watchlist. The first brief arrives at 06:00 on the
next weekday, or press **Run now** in the app.

Full walkthrough: [docs/DEPLOY.md](docs/DEPLOY.md). API keys: [docs/SETUP-APIS.md](docs/SETUP-APIS.md).

## What you get

- **Today**: the morning brief. Market context, your holdings, entry setups with the evidence
  behind each, the scored watchlist, risk flags, and what the run could not fetch.
- **Watchlist**: add, retire, tag, set a target entry. Adding a ticker validates it and backfills
  two years of prices.
- **Entries**: an entry-watch list with zones, re-checked four times a trading day.
- **Ticker**: charts, indicators, news, earnings, fundamentals and the score history for one name.
- **Runs**: every pipeline execution with counts, quota use and what degraded.
- **Settings**: scoring weights, quotas, analyst models and providers, delivery.
- `/m` is a phone layout of the same thing. Telegram delivery is optional.

![The Today screen: run health, holdings, entry watch and the morning brief](docs/images/app/01-today.png)

The screenshots throughout the docs run on a demo dataset: the prices are a seeded random walk and
the headlines are fictional, so no figure in them is a real quote or anyone's real position.

More screens in [docs/SCREENS.md](docs/SCREENS.md), and every workflow canvas in
[docs/WORKFLOWS.md](docs/WORKFLOWS.md).

## How it fits together

```
n8n                         app (FastAPI + SPA)          Postgres
 Daily 06:00 weekdays  ->    /api/reports/.../analyze  ->  intel.*    scores, reports, runs
 six collectors             analyst (LangGraph)           intraday.*  optional minute bars
 Entry Signals 4x/day  ->    /api/entries/signal
 Backfill on demand    <-    watchlist changes
```

| Piece | Where | Notes |
|---|---|---|
| Pipeline | `workflows/` | 9 daily workflows + 3 intraday, imported and activated by bootstrap |
| App and analyst | `app/` | FastAPI, a single-file SPA, a LangGraph analyst with read-only SQL tools |
| Schema | `sql/` | numbered migrations applied by `scripts/migrate.py` |
| Recorder | `intraday/` | optional 1-minute bar recorder, opt-in compose profile |
| Deploy | `compose.yml`, `deploy/` | Compose is the reference; Kubernetes manifests in `deploy/k8s/` |

## Documentation

| Doc | What it covers |
|---|---|
| [docs/SETUP-APIS.md](docs/SETUP-APIS.md) | every API key: where to get it, the free-tier limits, what breaks without it |
| [docs/DEPLOY.md](docs/DEPLOY.md) | the Compose deploy end to end, ports, `.env`, upgrades |
| [docs/WATCHLIST.md](docs/WATCHLIST.md) | the yaml format, tags, benchmarks, holdings, backfill |
| [docs/WORKFLOWS.md](docs/WORKFLOWS.md) | the workflow catalog and how to edit and redeploy one |
| [docs/SCORING.md](docs/SCORING.md) | the four score components, the flags, and the coverage rule |
| [docs/ANALYST.md](docs/ANALYST.md) | the agent graph, its tools, grounding, spend caps |
| [docs/DATA-MODEL.md](docs/DATA-MODEL.md) | tables, roles, migration order |
| [docs/NOTIFICATIONS.md](docs/NOTIFICATIONS.md) | Telegram, and an SMTP drop-in if you prefer mail |
| [docs/INTRADAY.md](docs/INTRADAY.md) | the optional minute-bar recorder and its three workflows |
| [docs/CANVAS.md](docs/CANVAS.md) | how the workflow canvases document themselves |
| [docs/UPGRADING.md](docs/UPGRADING.md) | version pins and how to move forward |

## Requirements

Docker with Compose, about 2 GB of RAM and 5 GB of disk to start, Python 3.11+ on the host for the
scripts, and free accounts at Alpaca, Finnhub and Alpha Vantage. An LLM provider is optional: the
pipeline and scores work without one, and Ollama makes the analyst free and local.

## Contributing

Issues and pull requests are welcome; see [CONTRIBUTING.md](CONTRIBUTING.md). One rule up front:
nothing that places, amends or cancels a broker order will be merged.

MIT licensed. [LICENSE](LICENSE).
