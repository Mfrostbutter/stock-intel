-- Pin the search path on every trigger function. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 018_trigger_search_path.sql)
--
-- Without this a caller whose search_path does not include intel (the migration runner connects as
-- the superuser, and so does psql by default) hits "relation config_history does not exist" from
-- inside the trigger, because the function body resolves unqualified names at run time against the
-- caller's path.
SET search_path TO intel, public;

ALTER FUNCTION log_config_change()       SET search_path = intel, public;
ALTER FUNCTION log_position_change()     SET search_path = intel, public;
ALTER FUNCTION sync_holding_tag()        SET search_path = intel, public;
ALTER FUNCTION log_entry_watch_change()  SET search_path = intel, public;
