-- Stock Intel intraday fork schema. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 011_intraday_schema.sql
--
-- Owns the `intraday` schema: recorder tables plus the append-only ledgers described in
-- docs/INTRADAY.md, which nothing in this repository writes.
-- Boundary: the intraday service NEVER writes intel.*; it reads dated context through the
-- read-only role stocks_intraday_ro. n8n NEVER writes the trading ledgers (order/fill/cash
-- belong to the execution controller). Money is numeric, times are timestamptz (UTC storage),
-- provider payloads are jsonb. Corrections append as new rows; history is never overwritten.
--
-- Login roles are created by deploy/postgres/init.sh with passwords from .env:
--   stocks_intraday_svc  (STOCKS_INTRADAY_SVC_PASSWORD)  persistent service: DML on intraday.*
--   stocks_intraday_ro   (STOCKS_INTRADAY_RO_PASSWORD)   context reader: SELECT on intel.*
-- The DO blocks below only grant when they find the role.

CREATE SCHEMA IF NOT EXISTS intraday;
SET search_path TO intraday, intel, public;

-- ============================================================================
-- Recorder tables (WF-01..09 + persistent collector write these)
-- ============================================================================

-- Frozen session plan. Actionable universe committed at OPEN-5m; config_hash pins the run.
CREATE TABLE IF NOT EXISTS session_manifests (
  session_id        uuid PRIMARY KEY,
  trade_date        date NOT NULL,
  open_at           timestamptz NOT NULL,
  close_at          timestamptz NOT NULL,
  early_close       boolean NOT NULL DEFAULT false,
  calendar_version  bigint,
  universe_id       text NOT NULL DEFAULT 'intraday_movers',
  discovery_cov     jsonb,                       -- coverage, ranks, exclusions, timestamps
  ranking_version   text,
  membership_version text,
  feed_mode         text NOT NULL,               -- engineering | evaluation
  required_sources  text[] NOT NULL DEFAULT '{}',
  freshness_ms      jsonb,                        -- per-check thresholds
  event_windows     jsonb,                        -- earnings/macro block windows
  symbols           jsonb NOT NULL DEFAULT '[]',  -- [{symbol,cik,...}] capped active list + benchmarks
  config_hash       text NOT NULL,
  frozen_at         timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS session_manifests_date ON session_manifests (trade_date);

-- Point-in-time event store (news, filings, calendar, status). Corrections append.
CREATE TABLE IF NOT EXISTS source_events (
  id             bigserial PRIMARY KEY,
  session_id     uuid,
  source         text NOT NULL,                 -- alpaca_news | sec | calendar | issuer_rss | ...
  source_version text,
  event_type     text,
  symbol         text,
  cik            text,
  event_at       timestamptz,                   -- provider/reference time of the event
  published_at   timestamptz,
  first_seen_at  timestamptz NOT NULL DEFAULT now(),
  received_at    timestamptz NOT NULL DEFAULT now(),
  available_at   timestamptz NOT NULL DEFAULT now(),  -- first availability to THIS system
  feed           text,
  parser_version text,
  completeness   text,                          -- complete | partial | unknown
  payload_hash   text NOT NULL,
  payload        jsonb,
  UNIQUE (source, source_version, payload_hash) -- dedup by record identity + version/hash
);
CREATE INDEX IF NOT EXISTS source_events_sym_time ON source_events (symbol, available_at DESC);
CREATE INDEX IF NOT EXISTS source_events_session ON source_events (session_id);

-- Completed one-minute OHLCV bars with revisions. Keep every version; decisions use the
-- version available at decision time. A bar can be complete in time yet later revised.
CREATE TABLE IF NOT EXISTS bar_versions (
  id            bigserial PRIMARY KEY,
  session_id    uuid,
  symbol        text NOT NULL,
  bar_end       timestamptz NOT NULL,
  bar_interval  text NOT NULL DEFAULT '1min',
  version       int NOT NULL DEFAULT 1,
  is_correction boolean NOT NULL DEFAULT false,
  open          numeric(18,6),
  high          numeric(18,6),
  low           numeric(18,6),
  close         numeric(18,6),
  volume        bigint,
  vwap          numeric(18,6),
  trade_count   int,
  feed          text,
  available_at  timestamptz NOT NULL DEFAULT now(),
  received_at   timestamptz NOT NULL DEFAULT now(),
  payload_hash  text,
  UNIQUE (symbol, bar_end, bar_interval, version)
);
CREATE INDEX IF NOT EXISTS bar_versions_sym_end ON bar_versions (symbol, bar_end DESC);

-- Derived features per symbol per completed bar. Insufficient history yields null, not zero.
CREATE TABLE IF NOT EXISTS feature_batches (
  id               bigserial PRIMARY KEY,
  session_id       uuid,
  symbol           text NOT NULL,
  bar_end          timestamptz NOT NULL,
  feature_version  text NOT NULL,
  input_watermark  timestamptz,
  completeness     text,
  available_at     timestamptz NOT NULL DEFAULT now(),
  feature_values   jsonb NOT NULL,              -- opening_range, spread_bps, rvol, atr, ema, vwap, returns
  created_at       timestamptz NOT NULL DEFAULT now(),
  UNIQUE (symbol, bar_end, feature_version)
);
CREATE INDEX IF NOT EXISTS feature_batches_sym_end ON feature_batches (symbol, bar_end DESC);

-- Session/holiday/early-close calendar snapshots. Missing calendar is UNKNOWN, not a weekday.
CREATE TABLE IF NOT EXISTS calendar_versions (
  id           bigserial PRIMARY KEY,
  trade_date   date NOT NULL,
  open_at      timestamptz,
  close_at     timestamptz,
  early_close  boolean NOT NULL DEFAULT false,
  is_open      boolean,
  source       text NOT NULL,
  version      int NOT NULL DEFAULT 1,
  available_at timestamptz NOT NULL DEFAULT now(),
  payload      jsonb,
  UNIQUE (trade_date, source, version)
);

-- Job lease + fencing. An expired worker cannot overwrite a newer cursor.
CREATE TABLE IF NOT EXISTS ingestion_jobs (
  id             bigserial PRIMARY KEY,
  workflow       text NOT NULL,
  source         text NOT NULL,
  session_id     uuid,
  due_window     text NOT NULL,                 -- the (due-window) part of the claim key
  status         text NOT NULL DEFAULT 'pending', -- pending | claimed | done | failed
  lease_owner    text,
  fencing_token  bigint,                        -- monotonic; higher wins
  lease_expires  timestamptz,
  attempts       int NOT NULL DEFAULT 0,
  cursor_state   jsonb,
  claimed_at     timestamptz,
  completed_at   timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (workflow, source, session_id, due_window)
);
CREATE INDEX IF NOT EXISTS ingestion_jobs_status ON ingestion_jobs (status, lease_expires);

-- Per-source committed cursor. symbol '' = market-wide.
CREATE TABLE IF NOT EXISTS source_cursors (
  source        text NOT NULL,
  symbol        text NOT NULL DEFAULT '',
  window_end    timestamptz,
  cursor_state  jsonb,
  fencing_token bigint,
  updated_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (source, symbol)
);

-- Per-source / per-symbol collection health. A valid empty poll updates health, not events.
CREATE TABLE IF NOT EXISTS source_health (
  id                bigserial PRIMARY KEY,
  source            text NOT NULL,
  symbol            text NOT NULL DEFAULT '',
  checked_at        timestamptz NOT NULL DEFAULT now(),
  last_success_at   timestamptz,
  provider_age_s    numeric(12,3),              -- age of newest provider event
  publication_lag_s numeric(12,3),
  queue_age_s       numeric(12,3),
  duration_ms       int,
  missing_symbols   text[],
  reconnects        int,
  http_failures     int,
  quota_headroom    jsonb,
  storage_bytes     bigint,
  status            text                        -- ok | degraded | outage
);
CREATE INDEX IF NOT EXISTS source_health_src_time ON source_health (source, checked_at DESC);

-- Versioned daily/weekly report artifacts. Provisional at CLOSE+15m, reconciled at CLOSE+60m.
CREATE TABLE IF NOT EXISTS report_versions (
  id           bigserial PRIMARY KEY,
  session_id   uuid,
  trade_date   date NOT NULL,
  kind         text NOT NULL,                   -- daily | weekly
  version      int NOT NULL DEFAULT 1,
  provisional  boolean NOT NULL DEFAULT true,
  markdown     text,
  html         text,
  metrics      jsonb,
  generated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (trade_date, kind, version)
);

-- ============================================================================
-- Trading ledgers (execution controller writes; n8n WF-09 reads for reports)
-- ============================================================================

-- One row per trading session. Reset the ALLOCATION only; cumulative state lives on.
CREATE TABLE IF NOT EXISTS sessions (
  session_id          uuid PRIMARY KEY,
  trade_date          date NOT NULL,
  open_at             timestamptz,
  close_at            timestamptz,
  starting_allocation numeric(14,4) NOT NULL DEFAULT 50.0000,
  retained_principal  numeric(14,4),
  top_up_applied      numeric(14,4) NOT NULL DEFAULT 0,
  feed_mode           text,                     -- engineering | evaluation
  strategy_version_id bigint,
  profile_id          text NOT NULL DEFAULT 'baseline_1x',
  execution_label     text,                     -- engineering | hypothetical_margin_replay | eligible_account_paper_sleeve
  config_hash         text,
  status              text NOT NULL DEFAULT 'planned', -- planned | active | closing | closed | blocked
  flat_confirmed      boolean NOT NULL DEFAULT false,
  opened_at           timestamptz,
  closed_at           timestamptz
);
CREATE INDEX IF NOT EXISTS sessions_date ON sessions (trade_date);

-- Append-only cash ledger. Separate ledgers: trading cash, banked profit, loss top-up, opex.
-- A deposit/top-up is NEVER a return. idempotency_key makes sweep/reset events safe to retry.
CREATE TABLE IF NOT EXISTS cash_events (
  id               bigserial PRIMARY KEY,
  session_id       uuid,
  ledger           text NOT NULL,               -- trading_cash | banked_profit | loss_replenishment | operating
  event_type       text NOT NULL,               -- allocation | profit_sweep | loss_topup | operating_expense | financing | correction
  amount           numeric(14,4) NOT NULL,
  note             text,
  idempotency_key  text NOT NULL UNIQUE,
  event_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS cash_events_session ON cash_events (session_id, event_at);

-- Frozen strategy parameters. config_hash pins the exact rule set for a registered run.
CREATE TABLE IF NOT EXISTS strategy_versions (
  id           bigserial PRIMARY KEY,
  name         text NOT NULL,                   -- opening_range_breakout
  version      text NOT NULL,
  params       jsonb NOT NULL,                  -- windows, entry cap coeff, stop, target R, caps, gates
  config_hash  text NOT NULL UNIQUE,
  frozen_at    timestamptz NOT NULL DEFAULT now(),
  notes        text,
  UNIQUE (name, version)
);

-- Point-in-time inputs a decision consumed. available_at <= decision.observation_time.
CREATE TABLE IF NOT EXISTS input_events (
  id           bigserial PRIMARY KEY,
  session_id   uuid,
  decision_id  bigint,                          -- set once the decision row exists
  kind         text NOT NULL,                   -- bar | feature | quote | calendar | context
  input_ref    text NOT NULL,                   -- e.g. bar_versions:<id> | feature_batches:<id>
  available_at timestamptz,
  captured_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS input_events_decision ON input_events (decision_id);

-- ---------------------------------------------------------------------------
-- Paper-trading ledger tables: decisions, risk_checks, order_events, fills,
-- position_snapshots. Nothing in this repository writes them. They exist so a
-- future paper-trading study has a shape to write into, and they stay empty.
-- No code here places, amends or cancels an order, and a change that does is
-- out of scope (see CONTRIBUTING.md).
-- ---------------------------------------------------------------------------

-- Append-only decision ledger. Links input IDs, observation time, version/config hash, reason.
CREATE TABLE IF NOT EXISTS decisions (
  id                  bigserial PRIMARY KEY,
  session_id          uuid NOT NULL,
  symbol              text NOT NULL,
  strategy_version_id bigint,
  decision_at         timestamptz NOT NULL DEFAULT now(),
  observation_time    timestamptz,              -- the input watermark the decision saw
  config_hash         text,
  reason              text,
  proposed_size       numeric(18,8),
  final_size          numeric(18,8),
  entry_cap           numeric(18,6),
  stop_trigger        numeric(18,6),
  target              numeric(18,6),
  input_ids           jsonb,                    -- snapshot of the input refs
  outcome             text NOT NULL DEFAULT 'proposed', -- proposed | approved | rejected | expired
  created_at          timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS decisions_session ON decisions (session_id, decision_at);

-- Deterministic risk-gate results per decision. binding_constraint = the gate that bound size.
CREATE TABLE IF NOT EXISTS risk_checks (
  id                bigserial PRIMARY KEY,
  decision_id       bigint NOT NULL REFERENCES decisions(id) ON DELETE CASCADE,
  check_name        text NOT NULL,
  passed            boolean NOT NULL,
  binding_constraint boolean NOT NULL DEFAULT false,
  detail            jsonb,
  checked_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS risk_checks_decision ON risk_checks (decision_id);

-- Order state machine transitions. client_order_id is persistent (session/strategy/decision/leg).
-- A submission timeout is an UNKNOWN outcome, not permission to submit a duplicate.
CREATE TABLE IF NOT EXISTS order_events (
  id             bigserial PRIMARY KEY,
  session_id     uuid NOT NULL,
  decision_id    bigint REFERENCES decisions(id) ON DELETE SET NULL,
  client_order_id text NOT NULL,
  broker_order_id text,
  leg            text NOT NULL,                 -- entry | stop | target | liquidation
  state          text NOT NULL,                 -- proposed|risk_approved|reserved|submitted|acknowledged|partially_filled|filled|exit_pending|flat|reconciled|rejected|canceled|expired|unknown
  qty            numeric(18,8),
  filled_qty     numeric(18,8),
  notional       numeric(18,6),
  limit_price    numeric(18,6),
  stop_price     numeric(18,6),
  event_at       timestamptz NOT NULL DEFAULT now(),
  broker_ts      timestamptz,
  payload        jsonb
);
CREATE INDEX IF NOT EXISTS order_events_client ON order_events (client_order_id, event_at);
CREATE INDEX IF NOT EXISTS order_events_session ON order_events (session_id, event_at);

-- Executions. Dedup by broker execution id. Process late fills even after a cancel request.
CREATE TABLE IF NOT EXISTS fills (
  id             bigserial PRIMARY KEY,
  session_id     uuid,
  order_event_id bigint REFERENCES order_events(id) ON DELETE SET NULL,
  client_order_id text,
  broker_exec_id text NOT NULL UNIQUE,
  symbol         text NOT NULL,
  side           text NOT NULL,                 -- buy | sell
  qty            numeric(18,8) NOT NULL,
  price          numeric(18,6) NOT NULL,
  fee            numeric(14,6) NOT NULL DEFAULT 0,
  filled_at      timestamptz,
  received_at    timestamptz NOT NULL DEFAULT now(),
  raw            jsonb
);
CREATE INDEX IF NOT EXISTS fills_session ON fills (session_id, filled_at);

-- Position state over time. source distinguishes broker truth from local model.
CREATE TABLE IF NOT EXISTS position_snapshots (
  id             bigserial PRIMARY KEY,
  session_id     uuid,
  symbol         text NOT NULL,
  qty            numeric(18,8) NOT NULL,
  avg_price      numeric(18,6),
  market_value   numeric(18,6),
  unrealized_pnl numeric(14,4),
  source         text NOT NULL DEFAULT 'local', -- local | broker
  snapshot_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS position_snapshots_session ON position_snapshots (session_id, snapshot_at DESC);

-- Operational incidents. blocks_reset latches a session/experiment halt.
CREATE TABLE IF NOT EXISTS incidents (
  id           bigserial PRIMARY KEY,
  session_id   uuid,
  kind         text NOT NULL,                   -- stale_feed | unknown_order | orphan_order | position_mismatch | overnight_exception | kill_switch
  severity     text NOT NULL DEFAULT 'warn',    -- warn | error | critical
  detail       jsonb,
  blocks_reset boolean NOT NULL DEFAULT false,
  opened_at    timestamptz NOT NULL DEFAULT now(),
  resolved_at  timestamptz
);
CREATE INDEX IF NOT EXISTS incidents_open ON incidents (session_id) WHERE resolved_at IS NULL;

-- Registered experiments. profile_id + execution_label + window define a trial.
CREATE TABLE IF NOT EXISTS experiment_runs (
  id                  bigserial PRIMARY KEY,
  strategy_version_id bigint REFERENCES strategy_versions(id) ON DELETE SET NULL,
  profile_id          text NOT NULL DEFAULT 'baseline_1x',
  execution_label     text NOT NULL,           -- engineering | hypothetical_margin_replay | eligible_account_paper_sleeve
  window_start        date,
  window_end          date,
  config_hash         text,
  status              text NOT NULL DEFAULT 'registered', -- registered | running | complete | halted
  notes               text,
  created_at          timestamptz NOT NULL DEFAULT now()
);

-- Daily report metrics. Raw and adjusted P&L kept separately; append corrections.
CREATE TABLE IF NOT EXISTS daily_metrics (
  id                bigserial PRIMARY KEY,
  session_id        uuid,
  trade_date        date NOT NULL,
  opening_allocation numeric(14,4),
  entry_dollars_used numeric(14,4),
  raw_pnl           numeric(14,4),
  adjusted_pnl      numeric(14,4),
  fees              numeric(14,4),
  slippage          numeric(14,4),
  profit_banked     numeric(14,4),
  topup_due         numeric(14,4),
  topup_paid        numeric(14,4),
  cum_bank          numeric(14,4),
  cum_topups        numeric(14,4),
  cum_trading_pnl   numeric(14,4),
  cum_economic_pnl  numeric(14,4),
  drawdown          numeric(14,4),
  trade_count       int,
  no_trade          boolean,
  rejected_count    int,
  incident_count    int,
  data_coverage     jsonb,
  flat_confirmed    boolean,
  provisional       boolean NOT NULL DEFAULT true,
  version           int NOT NULL DEFAULT 1,
  computed_at       timestamptz NOT NULL DEFAULT now()
);
-- Append-only: corrections add rows; read the latest by computed_at. No unique on trade_date.
CREATE INDEX IF NOT EXISTS daily_metrics_date ON daily_metrics (trade_date, computed_at DESC);

-- ============================================================================
-- Roles
-- ============================================================================

-- Persistent service (collector + features + execution controller): owns intraday.* writes.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_intraday_svc') THEN
    RAISE NOTICE 'role stocks_intraday_svc missing; set STOCKS_INTRADAY_SVC_PASSWORD in .env and re-create the database, or CREATE ROLE stocks_intraday_svc LOGIN PASSWORD ... by hand';
  ELSE
    GRANT USAGE ON SCHEMA intraday TO stocks_intraday_svc;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intraday TO stocks_intraday_svc;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intraday TO stocks_intraday_svc;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intraday GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stocks_intraday_svc;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intraday GRANT USAGE, SELECT ON SEQUENCES TO stocks_intraday_svc;
    ALTER ROLE stocks_intraday_svc SET search_path = intraday, intel, public;
  END IF;
END $$;

-- Context reader: what the trading agent reads intel.* dated context through. Read-only.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_intraday_ro') THEN
    RAISE NOTICE 'role stocks_intraday_ro missing; set STOCKS_INTRADAY_RO_PASSWORD in .env and re-create the database, or CREATE ROLE stocks_intraday_ro LOGIN PASSWORD ... by hand';
  ELSE
    GRANT USAGE ON SCHEMA intel TO stocks_intraday_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA intel TO stocks_intraday_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT SELECT ON TABLES TO stocks_intraday_ro;
    -- read-only view of its own trading ledgers for report joins, no intraday writes
    GRANT USAGE ON SCHEMA intraday TO stocks_intraday_ro;
    GRANT SELECT ON ALL TABLES IN SCHEMA intraday TO stocks_intraday_ro;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intraday GRANT SELECT ON TABLES TO stocks_intraday_ro;
    ALTER ROLE stocks_intraday_ro SET search_path = intel, intraday, public;
    ALTER ROLE stocks_intraday_ro SET default_transaction_read_only = on;
    ALTER ROLE stocks_intraday_ro SET statement_timeout = '5s';
  END IF;
END $$;

-- n8n recorder-write grants (the intraday workflows write recorder tables and read the ledgers)
-- live in sql/012.
