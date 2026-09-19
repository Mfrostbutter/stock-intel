#!/bin/bash
# Runs once, on an empty Postgres volume. Creates the two databases and the service roles from
# the environment. Schema objects are not created here; scripts/migrate.py owns those so the
# same path works for upgrades.
set -euo pipefail

psql_su() { psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres "$@"; }

create_role() {
  local role="$1" pw="$2"
  [ -n "$pw" ] || { echo "init: $role has no password in .env, skipped"; return; }
  psql_su -c "DO \$\$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$role') THEN
      CREATE ROLE $role LOGIN PASSWORD '$pw';
    ELSE
      ALTER ROLE $role LOGIN PASSWORD '$pw';
    END IF;
  END \$\$;"
}

create_db() {
  local db="$1"
  psql_su -tAc "SELECT 1 FROM pg_database WHERE datname = '$db'" | grep -q 1 \
    || psql_su -c "CREATE DATABASE $db"
}

create_db "${STOCKS_PG_DB:-stocks}"
create_db "${N8N_DB_NAME:-n8n}"

create_role stocks_n8n           "${STOCKS_PG_PASSWORD:-}"
create_role stocks_app           "${STOCKS_APP_PG_PASSWORD:-}"
create_role stocks_analyst       "${STOCKS_ANALYST_PG_PASSWORD:-}"
create_role stocks_intraday_svc  "${STOCKS_INTRADAY_SVC_PASSWORD:-}"
create_role stocks_intraday_ro   "${STOCKS_INTRADAY_RO_PASSWORD:-}"
create_role "${N8N_DB_USER:-n8n}" "${N8N_DB_PASSWORD:-}"

# n8n owns its own database outright.
psql_su -c "ALTER DATABASE ${N8N_DB_NAME:-n8n} OWNER TO ${N8N_DB_USER:-n8n}"

echo "init: databases and roles ready"
