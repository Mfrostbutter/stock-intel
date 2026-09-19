# Watchlist

The watchlist is the one thing that is entirely yours. Everything else in the pipeline is driven by
it: what gets priced, what gets news, what gets scored, what appears in the brief.

It lives in `intel.watchlist` in the database. A file seeds it; after that the app owns it.

## The file

`watchlist.yaml` at the repo root, copied from `watchlist.example.yaml`:

```yaml
benchmarks: [SPY, QQQ]

tickers:
  - {ticker: AAPL,  tags: [tech]}
  - {ticker: NVDA,  tags: [semis], notes: "core position"}
  - {ticker: NEE,   tags: [utilities], target_entry: 60}
  - MSFT                                  # a bare symbol is fine
```

| Field | Meaning |
|---|---|
| `benchmarks` | at least one. Scored for context, never flagged, and relative strength is measured against them |
| `ticker` | the symbol, case-insensitive |
| `tags` | free text, as many as you like. Yours to invent |
| `notes` | anything you want to read later in the UI |
| `target_entry` | a price you are waiting for. Within 3% raises the `near-target` flag |

Then:

```bash
python scripts/seed_watchlist.py            # add and update
python scripts/seed_watchlist.py --dry-run  # show what it would do
```

Each symbol is validated against Finnhub before it is written, its CIK is filled from the SEC
ticker map, and anything with no stored prices triggers a two-year backfill through n8n.

The seeder never retires a row that has dropped out of the file, because the app owns retirement
and you will forget the file exists within a week. If you would rather the file be the source of
truth, `--retire-missing` retires whatever is not listed, except tickers you hold.

## Two reserved tags

- **`benchmark`** comes from the `benchmarks` list, or from the toggle in the app. A benchmark is
  priced and scored so you have something to compare against, and never appears as a setup.
- **`holding`** is derived from your Positions and cannot be typed in. Add a position in the app and
  the tag appears; close it and the tag goes. The file loader rejects it outright, and the API
  strips it from any tag edit.

## Editing in the app

The Watchlist screen is the everyday path: add a ticker (validated and backfilled on the spot),
retire one, edit tags, name, notes and target entry, or flip the benchmark toggle. Target entry and
notes are editable inline in the table; the pencil opens the rest.

Retiring is not deleting. The row stays with `active = false`, so history and past scores survive,
and re-adding it revives the same row. A ticker with an open position cannot be retired until you
close the position.

## What happens when you add a ticker

1. Finnhub validates the symbol and returns the company name.
2. The row is written, and the CIK is filled if the SEC knows the symbol (ETFs have none).
3. A backfill fires: two years of daily bars, then 600 days of indicators.
4. On the next daily run it picks up news, sentiment, earnings, fundamentals and signals.

So the first brief after adding a ticker shows momentum but leaves `value` and `catalyst` null.
That is the coverage rule doing its job, not a bug: a component stays null until at least half its
inputs exist. See [SCORING.md](SCORING.md).

## Size

A 50-ticker watchlist fits comfortably in the free tiers: roughly 55 Alpaca calls, 250 Finnhub
calls and 22 Alpha Vantage calls a day. Twice that is fine for Alpaca and Finnhub but sentiment
coverage thins, because Alpha Vantage's 25 calls a day are the binding constraint. Several hundred
tickers would need a paid data plan and some patience with the news collector.
