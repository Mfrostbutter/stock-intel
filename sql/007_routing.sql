-- Stock Intel analyst: OpenRouter routing preference and critic fallback. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 007_routing.sql
-- analyst_provider_prefs merges into OpenRouter's provider object (data_collection=deny is always added by code).
-- analyst_critic_fallback_model takes the critique pass when the cheap model errors twice.

SET search_path TO intel, public;

INSERT INTO config (key, value) VALUES
  ('analyst_provider_prefs', '{"sort":"throughput"}'::jsonb),
  ('analyst_critic_fallback_model', '"anthropic/claude-haiku-4.5"'::jsonb)
ON CONFLICT (key) DO NOTHING;
