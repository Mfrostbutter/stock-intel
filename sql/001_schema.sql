-- Stock Intel schema. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 001_schema.sql

CREATE SCHEMA IF NOT EXISTS intel;
SET search_path TO intel, public;

-- Curated watchlist. The one hand-edited table.
CREATE TABLE IF NOT EXISTS watchlist (
  ticker        text PRIMARY KEY,
  name          text,
  tags          text[] NOT NULL DEFAULT '{}',
  cik           text,
  added_at      timestamptz NOT NULL DEFAULT now(),
  active        boolean NOT NULL DEFAULT true,
  target_entry  numeric(12,4),
  notes         text
);

-- One row per pipeline execution.
CREATE TABLE IF NOT EXISTS runs (
  run_id      uuid PRIMARY KEY,
  kind        text NOT NULL,                 -- daily | discover | backfill
  as_of       date NOT NULL,
  started_at  timestamptz NOT NULL DEFAULT now(),
  finished_at timestamptz,
  status      text NOT NULL DEFAULT 'running',
  summary     jsonb
);

-- Quota ledger + silent-failure detector.
CREATE TABLE IF NOT EXISTS api_usage (
  id          bigserial PRIMARY KEY,
  run_id      uuid,
  source      text NOT NULL,
  endpoint    text,
  ticker      text,
  status      text NOT NULL,                 -- ok | error | quota_skip
  http_code   int,
  ms          int,
  called_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS api_usage_source_day ON api_usage (source, called_at);

CREATE TABLE IF NOT EXISTS prices_daily (
  ticker  text NOT NULL,
  as_of   date NOT NULL,
  open    numeric(14,4),
  high    numeric(14,4),
  low     numeric(14,4),
  close   numeric(14,4),
  volume  bigint,
  source  text,
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS indicators_daily (
  ticker          text NOT NULL,
  as_of           date NOT NULL,
  sma20           numeric(14,4),
  sma50           numeric(14,4),
  sma200          numeric(14,4),
  rsi14           numeric(6,2),
  atr14           numeric(14,4),
  vol_z20         numeric(8,3),
  hi52w           numeric(14,4),
  lo52w           numeric(14,4),
  pct_from_hi52w  numeric(8,3),
  ret_1d          numeric(8,4),
  ret_5d          numeric(8,4),
  ret_20d         numeric(8,4),
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS news_articles (
  id             text PRIMARY KEY,           -- source-native id or sha1(url)
  ticker         text NOT NULL,
  published_at   timestamptz,
  source         text NOT NULL,              -- finnhub | marketaux | alphavantage
  headline       text,
  url            text,
  summary        text,
  sentiment      numeric(5,3),               -- -1..1
  sentiment_src  text,                       -- finnhub | marketaux | av | llm
  raw            jsonb,
  ingested_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS news_ticker_pub ON news_articles (ticker, published_at DESC);

CREATE TABLE IF NOT EXISTS sentiment_daily (
  ticker          text NOT NULL,
  as_of           date NOT NULL,
  news_score      numeric(5,3),
  news_count      int,
  social_score    numeric(5,3),
  social_count    int,
  av_topic_score  numeric(5,3),
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS fundamentals (
  ticker          text NOT NULL,
  as_of           date NOT NULL,
  market_cap      numeric(20,2),
  pe              numeric(12,3),
  ps              numeric(12,3),
  ev_ebitda       numeric(12,3),
  ev_rev          numeric(12,3),
  fcf_yield       numeric(8,4),
  rev_growth_yoy  numeric(8,4),
  gross_margin    numeric(8,4),
  cash            numeric(20,2),
  debt            numeric(20,2),
  burn_qtr        numeric(20,2),
  runway_qtrs     numeric(8,2),
  source          text,
  raw             jsonb,
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS analyst_recs (
  ticker      text NOT NULL,
  as_of       date NOT NULL,
  strong_buy  int, buy int, hold int, sell int, strong_sell int,
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS earnings_calendar (
  ticker        text NOT NULL,
  report_date   date NOT NULL,
  eps_est       numeric(12,4),
  eps_actual    numeric(12,4),
  rev_est       numeric(20,2),
  rev_actual    numeric(20,2),
  surprise_pct  numeric(8,3),
  PRIMARY KEY (ticker, report_date)
);

CREATE TABLE IF NOT EXISTS insider_tx (
  id        text PRIMARY KEY,
  ticker    text NOT NULL,
  filed_at  date,
  insider   text,
  tx_type   text,                            -- P purchase | S sale | other
  shares    bigint,
  price     numeric(14,4),
  value     numeric(20,2)
);
CREATE INDEX IF NOT EXISTS insider_ticker_date ON insider_tx (ticker, filed_at DESC);

CREATE TABLE IF NOT EXISTS filings (
  accession    text PRIMARY KEY,
  ticker       text NOT NULL,
  cik          text,
  form         text,
  filed_at     date,
  url          text,
  items        text[],
  llm_summary  text
);
CREATE INDEX IF NOT EXISTS filings_ticker_date ON filings (ticker, filed_at DESC);

CREATE TABLE IF NOT EXISTS social_daily (
  ticker           text NOT NULL,
  as_of            date NOT NULL,
  st_messages      int,
  st_bull          int,
  st_bear          int,
  reddit_mentions  int,
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS macro_daily (
  as_of       date PRIMARY KEY,
  us10y       numeric(8,4),
  vix         numeric(8,4),
  fedfunds    numeric(8,4),
  qqq_close   numeric(14,4),
  soxx_close  numeric(14,4),
  arkx_close  numeric(14,4),
  spy_close   numeric(14,4)
);

CREATE TABLE IF NOT EXISTS scores_daily (
  ticker     text NOT NULL,
  as_of      date NOT NULL,
  momentum   numeric(6,2),
  value      numeric(6,2),
  sentiment  numeric(6,2),
  catalyst   numeric(6,2),
  composite  numeric(6,2),
  flags      text[] NOT NULL DEFAULT '{}',
  risks      text[] NOT NULL DEFAULT '{}',
  reasons    jsonb,
  PRIMARY KEY (ticker, as_of)
);

CREATE TABLE IF NOT EXISTS candidates (
  ticker      text NOT NULL,
  found_on    date NOT NULL,
  screen      text,
  value_rank  int,
  thesis      text,
  counter     text,
  status      text NOT NULL DEFAULT 'new',   -- new | watching | promoted | rejected
  PRIMARY KEY (ticker, found_on)
);

CREATE TABLE IF NOT EXISTS reports (
  as_of     date PRIMARY KEY,
  run_id    uuid,
  markdown  text,
  html      text,
  sent_to   text[],
  sent_at   timestamptz
);

-- Scoring weights and thresholds, editable without a redeploy.
CREATE TABLE IF NOT EXISTS config (
  key         text PRIMARY KEY,
  value       jsonb NOT NULL,
  updated_at  timestamptz NOT NULL DEFAULT now()
);
INSERT INTO config (key, value) VALUES
  ('weights', '{"momentum":0.30,"value":0.25,"sentiment":0.25,"catalyst":0.20}'),
  ('quota',   '{"finnhub":3000,"fmp":220,"alphavantage":22,"marketaux":90,"polygon":250,"alpaca":5000,"twelvedata":700}')
ON CONFLICT (key) DO NOTHING;

-- The watchlist starts empty. Seed it from watchlist.yaml with scripts/seed_watchlist.py,
-- or add tickers in the app's Watchlist screen.

-- App role: n8n reads and writes the intel schema only.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    RAISE NOTICE 'role stocks_n8n missing; create it first with CREATE ROLE stocks_n8n LOGIN PASSWORD ...';
  ELSE
    GRANT USAGE ON SCHEMA intel TO stocks_n8n;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intel TO stocks_n8n;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel TO stocks_n8n;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stocks_n8n;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT USAGE, SELECT ON SEQUENCES TO stocks_n8n;
    ALTER ROLE stocks_n8n SET search_path = intel, public;
  END IF;
END $$;
