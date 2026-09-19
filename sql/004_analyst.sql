-- Stock Intel analyst agent. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 004_analyst.sql
--
-- Role stocks_analyst must exist first: CREATE ROLE stocks_analyst LOGIN PASSWORD '...'
-- (password STOCKS_ANALYST_PG_PASSWORD from .env). The DO block below only grants when it finds it.

SET search_path TO intel, public;

-- One row per citation the analyst made. ref is the evidence id the model cited.
CREATE TABLE IF NOT EXISTS analysis_sources (
  id            bigserial PRIMARY KEY,
  analysis_id   bigint NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  ref           text NOT NULL,                      -- db:scores:NVDA:2026-09-08 | web:3 | filing:... | calc:1
  kind          text NOT NULL,                      -- db | web | filing | calc
  url           text,
  domain        text,
  published_at  timestamptz,
  excerpt       text,
  payload       jsonb,                              -- the evidence row as the model saw it
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS analysis_sources_analysis ON analysis_sources (analysis_id);

-- Fetched article cache.
CREATE TABLE IF NOT EXISTS article_texts (
  url         text PRIMARY KEY,
  domain      text,
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  title       text,
  text        text,
  summary     text,
  sentiment   numeric(5,3),
  ticker      text
);

-- Ask-the-analyst questions.
CREATE TABLE IF NOT EXISTS analysis_questions (
  id           bigserial PRIMARY KEY,
  ticker       text,
  question     text NOT NULL,
  analysis_id  bigint REFERENCES analyses(id) ON DELETE SET NULL,
  asked_at     timestamptz NOT NULL DEFAULT now(),
  answered_at  timestamptz
);

-- Bake-off tagging and run accounting on analyses.
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS experiment   text;      -- null for production runs
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS mode         text NOT NULL DEFAULT 'daily';  -- daily | question | deep
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS prompt_hash  text;      -- sha256 of the prompt set
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS turns        int;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS tool_calls   int;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS fetches      int;
ALTER TABLE analyses ADD COLUMN IF NOT EXISTS stripped     int;       -- claims removed by critique
CREATE INDEX IF NOT EXISTS analyses_experiment ON analyses (experiment, as_of) WHERE experiment IS NOT NULL;

-- Blind pairwise ratings between two analyses of the same day.
CREATE TABLE IF NOT EXISTS model_evals (
  id          bigserial PRIMARY KEY,
  experiment  text,
  as_of       date NOT NULL,
  analysis_a  bigint NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  analysis_b  bigint NOT NULL REFERENCES analyses(id) ON DELETE CASCADE,
  preferred   bigint REFERENCES analyses(id) ON DELETE SET NULL,
  note        text,
  rated_at    timestamptz NOT NULL DEFAULT now()
);

-- Config rows. Model ids are OpenRouter ids (<vendor>/<model>); OpenRouter is the only provider.
INSERT INTO config (key, value) VALUES
  ('news_domains', '["reuters.com","cnbc.com","marketwatch.com","finance.yahoo.com","fool.com","benzinga.com","investing.com","techcrunch.com","arstechnica.com","spacenews.com","theregister.com","semianalysis.com","sec.gov"]'::jsonb),
  ('analyst_hitl',                    'false'::jsonb),
  ('analyst_max_fetches',             '8'::jsonb),
  ('analyst_search_provider',         '"brave"'::jsonb),
  ('analyst_cheap_model',             '"anthropic/claude-haiku-4.5"'::jsonb),
  ('analyst_share_positions_offsite', 'false'::jsonb),
  ('analyst_bakeoff_models',          '["deepseek/deepseek-v4-pro-0813","z-ai/glm-5.3","moonshotai/kimi-k3","openai/gpt-5.6-luna"]'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- 002 seeded bare Anthropic ids; move them to OpenRouter ids. Only touches the seeded values.
UPDATE config SET value = '"anthropic/claude-sonnet-5"'::jsonb WHERE key = 'analyst_model'      AND value = '"claude-sonnet-5"'::jsonb;
UPDATE config SET value = '"anthropic/claude-opus-5"'::jsonb   WHERE key = 'analyst_deep_model' AND value = '"claude-opus-5"'::jsonb;

-- App role gets the new tables (002's ALL TABLES grant predates them).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON analysis_sources, article_texts, analysis_questions, model_evals TO stocks_app;
    GRANT USAGE, SELECT ON SEQUENCE analysis_sources_id_seq, analysis_questions_id_seq, model_evals_id_seq TO stocks_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    GRANT SELECT ON analysis_sources, article_texts, analysis_questions, model_evals TO stocks_n8n;
  END IF;
END $$;

-- Read-only analyst role: what the model's database tools connect as.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_analyst') THEN
    RAISE NOTICE 'role stocks_analyst missing; create it first with CREATE ROLE stocks_analyst LOGIN PASSWORD ...';
  ELSE
    GRANT USAGE ON SCHEMA intel TO stocks_analyst;
    GRANT SELECT ON ALL TABLES IN SCHEMA intel TO stocks_analyst;
    ALTER DEFAULT PRIVILEGES IN SCHEMA intel GRANT SELECT ON TABLES TO stocks_analyst;
    ALTER ROLE stocks_analyst SET search_path = intel, public;
    ALTER ROLE stocks_analyst SET default_transaction_read_only = on;
    ALTER ROLE stocks_analyst SET statement_timeout = '5s';
  END IF;
END $$;
