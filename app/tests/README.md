# stock-intel tests

Two kinds, both run by CI (`.github/workflows/ci.yml`).

**Headless**: stub models, recorded rows, mocked HTTP, plain file checks. No key, no DB, no
network, so `python -m pytest tests -q` works from a fresh clone.

| File | Covers |
|---|---|
| `test_analyst_graph.py`, `test_entry_graph.py`, `test_analyst_tools.py` | the LangGraph analyst and its tools |
| `test_entries_rules.py` | the entry-signal rules |
| `test_openrouter_guard.py` | the data_collection=deny guard, which must never regress |
| `test_providers.py` | the provider registry, the four kinds, key resolution |
| `test_seed_watchlist.py` | the watchlist.yaml loader rules |
| `test_migrate.py` | migration numbering, the plan, the changed-file refusal |
| `test_workflows.py` | pinned ids, credential binding, zone specs, Telegram delivery |
| `test_health.py`, `test_spend_summary.py` | health checks and the spend meter |

**Endpoint** (`test_positions.py`, `test_entries.py`): run against a live app on a throwaway
Postgres, never against your real database, because they write and delete positions.

```bash
# 1. throwaway Postgres with the schema applied
docker run -d --name si-test-pg -e POSTGRES_PASSWORD=test -e POSTGRES_DB=stocks \
  -p 15432:5432 postgres:16
STOCKS_PG_HOST=localhost STOCKS_PG_PORT=15432 POSTGRES_PASSWORD=test \
  python ../scripts/migrate.py

# 2. the two tickers the tests expect, so no Finnhub call is needed
psql "postgresql://postgres:test@localhost:15432/stocks" -c \
  "INSERT INTO intel.watchlist (ticker, name) VALUES ('NVDA','NVIDIA'),('INTC','Intel') ON CONFLICT DO NOTHING;"

# 3. the app against it
docker build -t stock-intel:test . && docker run -d --name si-test-app \
  -e STOCK_INTEL_APP_TOKEN=testtoken123 -e STOCKS_PG_HOST=host.docker.internal \
  -e STOCKS_PG_PORT=15432 -e STOCKS_PG_USER=postgres -e STOCKS_APP_PG_PASSWORD=test \
  -p 18095:8095 stock-intel:test

# 4. run everything
STOCK_INTEL_TEST_URL=http://127.0.0.1:18095 STOCK_INTEL_TEST_TOKEN=testtoken123 \
  python -m pytest tests -q
```

Both env vars are required; the endpoint suite skips itself rather than guessing an endpoint.
