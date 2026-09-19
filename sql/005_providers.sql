-- Stock Intel model providers (Settings > Models). Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 005_providers.sql
--
-- Provider registry lives in config so the UI can add endpoints. Keys never land here: each
-- provider names the environment variable that carries its key, and .env carries the value.
-- Model references are "<provider_id>:<model_id>"; a bare model id means openrouter.

SET search_path TO intel, public;

INSERT INTO config (key, value) VALUES
  ('analyst_providers', '[{"id":"openrouter","name":"OpenRouter","kind":"openrouter","base_url":"https://openrouter.ai/api/v1","key_env":"OPEN_ROUTER_API_KEY","enabled":true}]'::jsonb)
ON CONFLICT (key) DO NOTHING;
