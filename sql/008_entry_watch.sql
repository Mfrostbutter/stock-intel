-- Stock Intel entry watch list. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 008_entry_watch.sql
--
-- entry_watch: tickers you are waiting on for an entry, with a zone and a thesis.
-- entry_signals: append-only, one row per signal pass per ticker. Latest row is the live signal,
-- the history is the grading set. Framework, not advice; nothing here places orders.

SET search_path TO intel, public;

CREATE TABLE IF NOT EXISTS entry_watch (
  ticker        text PRIMARY KEY REFERENCES watchlist(ticker) ON DELETE CASCADE,
  thesis        text,
  zone_low      numeric(14,4),
  zone_high     numeric(14,4),
  invalidation  numeric(14,4),                -- price below which the thesis is broken
  horizon       text NOT NULL DEFAULT 'weeks',
  status        text NOT NULL DEFAULT 'watching',   -- watching | triggered | entered | dropped
  notes         text,
  added_at      timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  added_by      text NOT NULL DEFAULT 'ui',
  CONSTRAINT entry_watch_horizon CHECK (horizon IN ('days','weeks','months')),
  CONSTRAINT entry_watch_status  CHECK (status IN ('watching','triggered','entered','dropped')),
  CONSTRAINT entry_watch_zone    CHECK (zone_low IS NULL OR zone_high IS NULL OR zone_low <= zone_high)
);

-- Append-only audit of every edit and status move.
CREATE TABLE IF NOT EXISTS entry_watch_history (
  id            bigserial PRIMARY KEY,
  ticker        text NOT NULL,
  status        text NOT NULL,
  zone_low      numeric(14,4),
  zone_high     numeric(14,4),
  invalidation  numeric(14,4),
  horizon       text,
  thesis        text,
  changed_at    timestamptz NOT NULL DEFAULT now(),
  changed_by    text NOT NULL DEFAULT 'ui'
);
CREATE INDEX IF NOT EXISTS entry_watch_history_ticker ON entry_watch_history (ticker, changed_at DESC);

CREATE OR REPLACE FUNCTION log_entry_watch_change() RETURNS trigger AS $fn$
DECLARE
  actor text := COALESCE(NULLIF(current_setting('stockintel.actor', true), ''), 'ui');
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.status IS NOT DISTINCT FROM NEW.status
     AND OLD.zone_low IS NOT DISTINCT FROM NEW.zone_low
     AND OLD.zone_high IS NOT DISTINCT FROM NEW.zone_high
     AND OLD.invalidation IS NOT DISTINCT FROM NEW.invalidation
     AND OLD.horizon IS NOT DISTINCT FROM NEW.horizon
     AND OLD.thesis IS NOT DISTINCT FROM NEW.thesis THEN
    RETURN NEW;
  END IF;
  NEW.updated_at := now();
  INSERT INTO entry_watch_history (ticker, status, zone_low, zone_high, invalidation, horizon, thesis, changed_by)
  VALUES (NEW.ticker, NEW.status, NEW.zone_low, NEW.zone_high, NEW.invalidation, NEW.horizon, NEW.thesis, actor);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS entry_watch_history_trg ON entry_watch;
CREATE TRIGGER entry_watch_history_trg BEFORE INSERT OR UPDATE ON entry_watch
  FOR EACH ROW EXECUTE FUNCTION log_entry_watch_change();

CREATE TABLE IF NOT EXISTS entry_signals (
  id              bigserial PRIMARY KEY,
  ticker          text NOT NULL,
  as_of           date NOT NULL,              -- session the evidence was read at
  signal          text NOT NULL,              -- enter | near | wait | avoid
  confidence      numeric(4,3),
  summary         text,                       -- one plain sentence
  rationale       jsonb,                      -- [{text, cites, levels}]
  invalidation    jsonb,                      -- {text, cites, levels}
  zone_check      text,                       -- holds | raise | lower | unclear
  suggested_zone  jsonb,                      -- {low, high} when the model disagrees with the zone
  watch_for       jsonb,                      -- [text] what would change the call
  rule            jsonb,                      -- rule-layer snapshot the model was shown
  ref_price       numeric(14,4),              -- close the signal was read against
  model           text,
  analysis_id     bigint REFERENCES analyses(id) ON DELETE SET NULL,
  cost_usd        numeric(8,4),
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT entry_signals_signal CHECK (signal IN ('enter','near','wait','avoid'))
);
CREATE INDEX IF NOT EXISTS entry_signals_ticker ON entry_signals (ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS entry_signals_as_of  ON entry_signals (as_of DESC);

-- Config. entry_signal_model null means "same as analyst_model".
INSERT INTO config (key, value) VALUES
  ('entry_signal_auto',   'true'::jsonb),
  ('entry_signal_model',  'null'::jsonb),
  ('entry_signal_batch',  '8'::jsonb),
  ('entry_near_pct',      '0.03'::jsonb)
ON CONFLICT (key) DO NOTHING;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON entry_watch, entry_watch_history, entry_signals TO stocks_app;
    GRANT USAGE, SELECT ON SEQUENCE entry_watch_history_id_seq, entry_signals_id_seq TO stocks_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    GRANT SELECT ON entry_watch, entry_watch_history, entry_signals TO stocks_n8n;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_analyst') THEN
    GRANT SELECT ON entry_watch, entry_watch_history, entry_signals TO stocks_analyst;
  END IF;
END $$;
