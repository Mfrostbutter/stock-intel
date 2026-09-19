-- Stock Intel app tables. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 002_app.sql

SET search_path TO intel, public;

-- Watchlist provenance + retirement. active stays the switch; retired_at is the audit trail.
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS retired_at timestamptz;
ALTER TABLE watchlist ADD COLUMN IF NOT EXISTS added_by   text NOT NULL DEFAULT 'seed';

-- Optional holdings. Hand-maintained in Settings, never synced from a broker.
CREATE TABLE IF NOT EXISTS positions (
  ticker      text PRIMARY KEY REFERENCES watchlist(ticker) ON DELETE CASCADE,
  qty         numeric(18,6) NOT NULL,
  avg_cost    numeric(14,4),
  opened_at   date,
  notes       text,
  updated_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per analyst pass over a brief.
CREATE TABLE IF NOT EXISTS analyses (
  id              bigserial PRIMARY KEY,
  as_of           date NOT NULL,
  run_id          uuid,
  model           text,
  prompt_version  text,
  status          text NOT NULL DEFAULT 'pending',  -- pending | ok | invalid | error | capped
  input_hash      text,
  output          jsonb,
  markdown        text,
  error           text,
  tokens_in       int,
  tokens_out      int,
  cost_usd        numeric(8,4),
  created_at      timestamptz NOT NULL DEFAULT now(),
  finished_at     timestamptz
);
CREATE INDEX IF NOT EXISTS analyses_as_of ON analyses (as_of DESC, created_at DESC);
-- Idempotent re-analysis: one successful row per (as_of, prompt_version, input_hash).
CREATE UNIQUE INDEX IF NOT EXISTS analyses_dedupe
  ON analyses (as_of, prompt_version, input_hash) WHERE status = 'ok';

CREATE TABLE IF NOT EXISTS analysis_feedback (
  id           bigserial PRIMARY KEY,
  analysis_id  bigint NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  rating       text NOT NULL,                  -- up | down
  note         text,
  created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analysis_feedback_analysis ON analysis_feedback (analysis_id);

-- Config change log. Every PUT /api/config write lands here.
CREATE TABLE IF NOT EXISTS config_history (
  id          bigserial PRIMARY KEY,
  key         text NOT NULL,
  old_value   jsonb,
  new_value   jsonb NOT NULL,
  changed_by  text,
  changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS config_history_key ON config_history (key, changed_at DESC);

CREATE OR REPLACE FUNCTION log_config_change() RETURNS trigger AS $fn$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.value IS NOT DISTINCT FROM NEW.value THEN
    RETURN NEW;
  END IF;
  INSERT INTO config_history (key, old_value, new_value)
  VALUES (NEW.key, CASE WHEN TG_OP = 'UPDATE' THEN OLD.value ELSE NULL END, NEW.value);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS config_history_trg ON config;
CREATE TRIGGER config_history_trg AFTER INSERT OR UPDATE ON config
  FOR EACH ROW EXECUTE FUNCTION log_config_change();

-- App-owned config rows. notify_email is read by anyone who wires their own mail node;
-- delivery itself is configured in Settings, not here.
INSERT INTO config (key, value) VALUES
  ('notify_email',           'true'::jsonb),
  ('analyst_model',          '"claude-sonnet-5"'::jsonb),
  ('analyst_deep_model',     '"claude-opus-5"'::jsonb),
  ('analyst_prompt_version', '"v1"'::jsonb),
  ('analyst_daily_cap_usd',  '2.00'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- App role: same grants as stocks_n8n so the two can be rotated independently.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_app') THEN
    RAISE NOTICE 'role stocks_app missing; create it first with CREATE ROLE stocks_app LOGIN PASSWORD ...';
  ELSE
    GRANT USAGE ON SCHEMA intel TO stocks_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intel TO stocks_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel TO stocks_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO stocks_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT USAGE, SELECT ON SEQUENCES TO stocks_app;
    ALTER ROLE stocks_app SET search_path = intel, public;
  END IF;
END $$;

-- stocks_n8n keeps write access to the new tables (Daily writes analyses rows via the app,
-- but Backfill and Daily both touch watchlist).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA intel TO stocks_n8n;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intel TO stocks_n8n;
  END IF;
END $$;
