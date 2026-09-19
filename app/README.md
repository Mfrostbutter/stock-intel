# The app

FastAPI service plus a single-file SPA: the brief, the watchlist, the entry-watch list, the ticker
screens and the LangGraph analyst. One container, one port, one bearer token.

Deploying it is [docs/DEPLOY.md](../docs/DEPLOY.md); this page is for working on it.

## Layout

```
app/
  Dockerfile  compose.yml  requirements.txt  .env.example
  stockintel/
    main.py       FastAPI app, lifespan, /health, /ui, /m
    auth.py       bearer dependency, fail-closed
    config.py     environment only, nothing hardcoded
    db.py         psycopg3 pool, dict rows, search_path=intel
    api.py        every /api route
    providers.py  the model provider registry and the chat-model factory
    finnhub.py    ticker validation on add, logged to api_usage
    alpaca.py     live quotes, one snapshot call for all symbols, 20s cache
    n8n.py        outbound run-now and backfill webhooks
    entries.py    entry-watch rules
    health.py     pipeline freshness checks
    analyst/      graph.py (pure factory), tools.py, evidence.py, schemas.py,
                  openrouter.py (the deny-flag client), service.py (wiring, persistence, caps)
  static/ui.html      the SPA: Alpine + Tailwind from a CDN, no build step
  static/mobile.html  the phone layout at /m
  prompts/            the analyst prompt set; the sha256 of the set is stored on every analysis
  tests/              see tests/README.md
```

## Running it against a local stack

```bash
docker compose up -d postgres          # from the repo root
cd app
pip install -r requirements.txt
STOCKS_PG_HOST=localhost STOCKS_PG_USER=postgres STOCKS_APP_PG_PASSWORD=... \
STOCK_INTEL_APP_TOKEN=dev-token \
  python -m uvicorn stockintel.main:app --reload --port 8095
```

Then `http://localhost:8095/ui` and paste the token once. The SPA keeps it in `localStorage`; every
call is checked server-side anyway.

## Endpoints

| Method | Path | What |
|---|---|---|
| GET | `/health` | open, no auth |
| GET | `/ui`, `/m` | the SPA and the phone layout |
| GET | `/api/reports`, `/api/reports/{as_of}`, `/api/reports/{as_of}/scores` | briefs and their score rows |
| POST | `/api/reports/{as_of}/analyze` | run the analyst (bearer or the webhook header, so n8n can call it) |
| GET POST PATCH | `/api/watchlist`, `/api/watchlist/{ticker}` | the watchlist |
| GET PUT DELETE | `/api/positions`, `/api/positions/{ticker}`, `/api/positions/{ticker}/history` | holdings |
| GET POST PATCH DELETE | `/api/entries`, `/api/entries/{ticker}` | the entry-watch list |
| POST | `/api/entries/signal` | run the entry pass (bearer or the webhook header) |
| POST | `/api/entries/{ticker}/accept-zone` | adopt the analyst's suggested zone |
| GET | `/api/tickers/{t}/prices`, `/news`, `/scores` | the ticker screen |
| GET | `/api/quotes?tickers=` | live prices, server-side cached |
| GET | `/api/runs`, POST `/api/runs/daily` | run history, and run now |
| GET | `/api/health/data` | did the last due session land; drives the Today banner |
| GET PUT | `/api/config` | every config row |
| GET PUT | `/api/providers`, POST `/api/providers/{id}/test` | the model provider registry |
| GET | `/api/models?provider=&refresh=` | live model catalog, 5-minute cache |
| GET | `/api/analyses`, `/api/analyses/{id}`, POST `/api/analyses/{id}/feedback` | analyst output and ratings |
| GET | `/api/analyst/spend` | today against the daily cap |

Everything under `/api` needs the bearer token. Two endpoints additionally accept the shared
webhook header, because n8n calls them.

## Things worth knowing

- `db.py` pins `client_encoding=UTF8`. Without it a connection opened outside the app inherits
  SQL_ASCII and psycopg hands back `bytes` for every text column.
- The SPA is deliberately one file with CDN dependencies: no build step, no toolchain, and you can
  read the whole thing. It will not win a bundle-size award.
- Retrieved news text is data, never instructions. The analyst prompt wraps the context pack in
  delimiters and states that the content is untrusted, and the analyst's database role cannot write.
- Live prices come from Alpaca's free IEX feed, polled while the tab is visible and cached
  server-side so extra tabs cost nothing.
- Tests: `python -m pytest tests -q`. See [tests/README.md](tests/README.md).
