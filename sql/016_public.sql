-- Public release additions: migration ledger, app callback URL, Telegram channel. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 016_public.sql)
SET search_path TO intel, public;

CREATE TABLE IF NOT EXISTS schema_migrations (
  filename    text PRIMARY KEY,
  sha256      text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now()
);

-- Where n8n reaches the app. The Daily CTE returns it, so no workflow hardcodes a hostname.
INSERT INTO config (key, value) VALUES
  ('app_base_url', '"http://app:8095"'),
  ('telegram', '{"chat_id":"","send_document":true}')
ON CONFLICT (key) DO NOTHING;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    GRANT SELECT ON schema_migrations TO stocks_n8n;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_app') THEN
    GRANT SELECT ON schema_migrations TO stocks_app;
  END IF;
END $$;
