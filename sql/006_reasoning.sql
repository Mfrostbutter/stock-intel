-- Stock Intel analyst: reasoning effort per graph step. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 006_reasoning.sql
-- Values low | medium | high | none, sent as OpenRouter's reasoning.effort. Code default matches this row.

SET search_path TO intel, public;

INSERT INTO config (key, value) VALUES
  ('analyst_reasoning', '{"plan":"low","gather":"medium","draft":"medium","critic":"low"}'::jsonb)
ON CONFLICT (key) DO NOTHING;
