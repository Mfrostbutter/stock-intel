# intraday recorder

The data-continuity foundation for the intraday fork (Increment 3). Records completed 1-min
bars and quote snapshots into the `intraday.*` schema with point-in-time timestamps, under a
single-writer lease. **Read-only market data. No orders, no trading, no broker mutation.**

Framework, not advice. Places no orders.

## What it does

IN: Alpaca IEX 1-min bars + latest quotes (REST, engineering mode) for a symbol set.
OUT: `intraday.bar_versions` (completed bars, revision-as-new-row), `intraday.source_events`
(quote snapshots with spread), `intraday.source_health` (per-poll heartbeat).
READS/WRITES DB as role `stocks_intraday_svc` (writes `intraday.*` only; zero access to
`intel.*`, proven in `sql/011`).
ON FAILURE: a dead feed degrades the poll (heartbeat `status=outage`, `http_failures`), it
never crashes the loop; a lost lease (fenced by a newer writer) exits the process.

## Timestamps (point-in-time discipline)

Each bar/quote records `received_at` (when we got it) and `available_at` (first availability to
this system); bars also carry `bar_end` (completion time) and `feed`. Corrections append as a
new `version` rather than overwriting. Nothing is rewritten in place.

## Single-writer lease

Claims a lease row in `intraday.ingestion_jobs` keyed on
`(workflow='recorder', source, session_id, due_window)` with a monotonic fencing token. A second
process cannot start while a live lease is held by another owner; a fenced writer exits.

## Run (local, no deploy)

Secrets and config come from the environment, or from `.env` at the repo root.

    python -m intraday --once --warmup-min 60      # one poll (smoke test)
    python -m intraday --warmup-min 30             # continuous poll loop (Ctrl-C to stop)

Knobs in `.env.example`: feed, symbols, cadence, lease TTL.

## Not this iteration

- Websocket streaming (alpaca-py `StockDataStream`) replaces the REST poll for lower latency.
- The real session universe comes from WF-01 movers discovery (this uses a fixed list).
- Feature computation (opening range, RVOL, ATR) -> `intraday.feature_batches`.
- Deployment as an always-on service (Compose `intraday` profile, or `deploy/k8s`).
- Any order/execution code (a separate, later, gated component).
