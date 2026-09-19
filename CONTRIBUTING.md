# Contributing

Issues and pull requests are welcome. This is a personal project shared because it was useful, so
expect a small maintainer and honest scope limits rather than a roadmap.

## The one hard rule

**No order placement.** Nothing that places, amends or cancels an order at a broker will be merged,
behind a flag or otherwise. Not paper, not "just the client library", not "off by default". The
project is a research framework and stays one, which is what lets it be shared without a compliance
conversation. See [DISCLAIMER.md](DISCLAIMER.md).

Everything around that is fair game: new data sources, better scoring, more analysis, nicer UI,
other notification channels, other deploy targets.

## Before you open a pull request

```bash
cd app && python -m pytest tests -q         # headless suite, no keys needed
python -m compileall -q scripts intraday
```

If you touched a workflow:

```bash
python scripts/canvas/zone_layout.py workflows/<name>.json workflows/zones/<name>.json
python scripts/canvas/layout_check.py workflows/<name>.json    # must print RESULT: ok
```

CI runs all of that plus a secret scan, docker builds and an endpoint suite against a throwaway
Postgres.

## House style

- **Every workflow is tagged and canvas-documented.** The deploy script enforces both; see
  [docs/CANVAS.md](docs/CANVAS.md).
- **Migrations are additive.** Next number, idempotent, never edit an applied file. The runner
  refuses a file whose checksum changed.
- **Secrets come from the environment.** No key in the database, no key in a workflow, no key in a
  log line. A provider names its environment variable; that is all the database learns.
- **Comments say what a thing is and does.** Reasoning belongs in the commit message.
- **Scores stay explainable.** If a new component cannot be traced back to stored rows in the
  `reasons` object, it does not belong in the score.

## Things that would genuinely help

- More data sources behind the existing collector shape (one sub-workflow, counts out).
- Backtests. `scripts/backtest_*.py` are thin; measuring whether a flag actually predicts anything
  is the most useful work available here.
- Docs improvements from a fresh install. If a step needed knowledge that is not written down, that
  is a bug worth reporting.

## Reporting a security issue

See [SECURITY.md](SECURITY.md). Do not open a public issue for a vulnerability.
