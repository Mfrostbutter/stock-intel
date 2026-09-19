-- Provider registry defaults for a public install. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 017_llm_providers.sql)
--
-- Keys still never land in the database: a provider names the environment variable that carries
-- its key, and a local model host (kind 'ollama') needs none at all.
SET search_path TO intel, public;

-- The env var names match .env. The older spellings keep working through an alias in the app.
UPDATE config
   SET value = '[{"id":"openrouter","name":"OpenRouter","kind":"openrouter","base_url":"https://openrouter.ai/api/v1","key_env":"OPENROUTER_API_KEY","enabled":true}]'::jsonb,
       updated_at = now()
 WHERE key = 'analyst_providers'
   AND value = '[{"id":"openrouter","name":"OpenRouter","kind":"openrouter","base_url":"https://openrouter.ai/api/v1","key_env":"OPEN_ROUTER_API_KEY","enabled":true}]'::jsonb;

-- The bake-off is opt-in: an empty list costs nothing on a fresh install.
UPDATE config SET value = '[]'::jsonb, updated_at = now()
 WHERE key = 'analyst_bakeoff_models'
   AND value = '["deepseek/deepseek-v4-pro-0813","z-ai/glm-5.3","moonshotai/kimi-k3","openai/gpt-5.6-luna"]'::jsonb;
