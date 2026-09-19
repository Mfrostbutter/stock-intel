# Data model

One database, `stocks`, two schemas. `intel` is the daily pipeline and the app; `intraday` is the
optional minute-bar side and is empty unless you turn it on.

## Schema `intel`

**Your inputs**

| Table | Holds |
|---|---|
| `watchlist` | ticker, name, tags, cik, target_entry, notes, active. The one table you edit |
| `positions`, `position_history` | what you hold. Drives the `holding` tag through a trigger |
| `entry_watch`, `entry_watch_history` | tickers you are waiting on, with zones and a thesis |
| `config`, `config_history` | weights, quotas, model choices, delivery settings. Every change is kept |

**Collected**

| Table | Holds |
|---|---|
| `prices_daily` | OHLCV per ticker per session |
| `indicators_daily` | SMA 20/50/200, RSI 14, ATR, volume z-score, 52-week position |
| `news_articles`, `sentiment_daily` | headlines with sentiment, and the daily roll-up |
| `earnings_calendar` | past and upcoming dates, estimates, surprises |
| `fundamentals` | the metrics snapshot per ticker |
| `insider_tx` | open-market insider transactions |
| `analyst_recs` | the rating distribution over time |

**Produced**

| Table | Holds |
|---|---|
| `scores_daily` | the four components, composite, flags, risks, `reasons`, `coverage`, `score_version` |
| `reports` | the brief as markdown and HTML, plus where it was sent |
| `entry_signals` | zone checks from the entry pass |
| `runs` | one row per pipeline execution: status, timings, the summary |
| `api_usage` | one row per external call. The quota ledger |
| `analyses`, `analysis_sources`, `analysis_feedback`, `analysis_questions`, `article_texts`, `model_evals` | the analyst's output, its citations and your feedback |
| `schema_migrations` | which migration files are applied, with checksums |

## Schema `intraday`

Twenty-one tables from `sql/011`. In practice:

| Written by | Tables |
|---|---|
| the recorder | `bar_versions`, `quotes`, `source_events`, `source_health`, `ingestion_jobs` (the lease) |
| WF-01 | scan snapshots, the trading calendar, `session_manifests` |
| WF-07 | `incidents` |

`decisions`, `risk_checks`, `order_events`, `fills` and `position_snapshots` are written by
nothing. They are the shape a future paper-trading study would need, and they stay empty. See
[DISCLAIMER.md](../DISCLAIMER.md).

## Roles

| Role | Rights |
|---|---|
| `stocks_n8n` | read and write `intel`, read `intraday`. The pipeline |
| `stocks_app` | read and write `intel`. The app |
| `stocks_analyst` | SELECT only, read-only transactions, 5-second statement timeout. The analyst's tools |
| `stocks_intraday_svc` | read and write `intraday`, no write access to `intel`. The recorder |
| `stocks_intraday_ro` | read `intraday`. For dashboards and ad-hoc queries |

`deploy/postgres/init.sh` creates all five from `.env` when the volume is first initialised. The
migrations grant to whichever of them exist, so a partial set is fine.

## Migrations

Numbered files in `sql/`, applied in order by `scripts/migrate.py`, which records each filename with
its sha256 in `intel.schema_migrations` and refuses to run a file that changed after it was applied.
Every file is idempotent on its own, so an existing database that predates the runner picks up the
ledger on the first run without re-creating anything.

```bash
python scripts/migrate.py --status     # what is applied, pending or changed
python scripts/migrate.py              # apply the pending ones
```

Adding a migration: next number, `IF NOT EXISTS` everywhere, grants wrapped in a `DO` block that
checks the role exists. Never edit an applied file; add the next one.

## Poking around

```bash
python scripts/db_query.py "SELECT ticker, composite, flags FROM scores_daily
  WHERE as_of = (SELECT max(as_of) FROM scores_daily) ORDER BY composite DESC LIMIT 10"
```

`db_query.py` connects as the pipeline role and prints rows. The app's `/api/*` endpoints cover the
same ground with auth if you would rather script against HTTP.
