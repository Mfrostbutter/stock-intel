# Intraday

Optional. A recorder that stores one-minute bars and quotes during market hours, plus three
workflows that prepare, watch and repair the session. Everything here is read-only market data.
Nothing places an order, and nothing ever will.

It is off by default because it is a different order of activity: a container polling every minute
and a workflow firing every minute, against the daily pipeline's one run a morning.

## Turning it on

```bash
docker compose --profile intraday up -d
python scripts/bootstrap.py --intraday
```

That starts the recorder and activates WF-01, WF-07 and WF-08. Knobs in `.env`:

| Variable | Default | Meaning |
|---|---|---|
| `SI_INTRADAY_FEED` | `iex` | free tier. `sip` needs a paid Alpaca plan |
| `SI_INTRADAY_SYMBOLS` | `SPY,QQQ` | the fixed universe the recorder follows |
| `SI_INTRADAY_POLL_SECONDS` | `60` | poll cadence |
| `SI_INTRADAY_LEASE_SECONDS` | `120` | how long a writer's lease is good for |

## The pieces

**Recorder** (`intraday/`): polls Alpaca for one-minute bars and the latest quotes for its symbol
list, writes `intraday.bar_versions` and `intraday.quotes`, and heartbeats into
`intraday.source_health`. It keeps every version of a bar rather than overwriting, because the
provider revises late bars and a study that silently loses the first version is a study you cannot
trust.

**WF-01 Session Prep**: at 06:00 and every five minutes from 08:00 to 09:55 it reads the Alpaca
calendar, scans movers and most-actives, ranks a candidate pool and freezes a session manifest at
the open. The frozen manifest is what a later study would be judged against, so it cannot change
once the session starts.

**WF-07 Health**: every minute inside an expected session window, it reads collection status and
raises incidents on a stale heartbeat, provider data older than two minutes, HTTP failures or an
outright outage. It is also registered as the error workflow for the intraday collectors, so a
crash lands in `intraday.incidents` rather than in a log nobody reads.

**WF-08 Gap Repair**: every five minutes it builds the expected minute grid for the session,
diffs it against what was stored, claims a lease and refetches the missing windows.

## Single writer

Two guards, on purpose:

- one replica with the `Recreate` strategy, so a redeploy never overlaps;
- a lease row in `intraday.ingestion_jobs` with a fencing token, which is the authoritative guard.
  A second process cannot start while a live lease is held, and a fenced writer exits.

The lease is what actually protects the data. The replica count is just good manners.

## Cost and limits

The free Alpaca plan gives the IEX tape, which is a slice of total volume rather than the
consolidated tape, and refuses SIP data less than 15 minutes old. For studying mechanics and
building the pipeline that is fine. For anything that depends on complete volume, you need the paid
plan, and then only `SI_INTRADAY_FEED=sip` changes.

At 60-second polling with a handful of symbols the recorder is quiet: a few hundred MB of bars a
year, single-digit CPU percent.

## The empty tables

`sql/011` creates `decisions`, `risk_checks`, `order_events`, `fills` and `position_snapshots`.
Nothing in this repository writes them. They are the shape a paper-trading study would need, kept
in the schema so the design is legible, and they stay empty. Contributions that add order placement,
amendment or cancellation are out of scope; see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Running it on Kubernetes

Manifests and a walkthrough in [`deploy/k8s/README.md`](../deploy/k8s/README.md). Same single-writer
rules apply.
