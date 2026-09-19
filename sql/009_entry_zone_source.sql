-- Stock Intel entry watch: analyst-proposed zones. Database: stocks. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 009_entry_zone_source.sql)
--
-- A row added without a zone gets one from the analyst's first pass. zone_source says whose it is;
-- a hand edit of the zone flips it back to user.

SET search_path TO intel, public;

ALTER TABLE entry_watch         ADD COLUMN IF NOT EXISTS zone_source text NOT NULL DEFAULT 'user';   -- user | analyst
ALTER TABLE entry_watch_history ADD COLUMN IF NOT EXISTS zone_source text;
ALTER TABLE entry_signals       ADD COLUMN IF NOT EXISTS suggested_invalidation numeric(14,4);

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entry_watch_zone_source') THEN
    ALTER TABLE entry_watch ADD CONSTRAINT entry_watch_zone_source CHECK (zone_source IN ('user', 'analyst'));
  END IF;
END $$;

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
     AND OLD.thesis IS NOT DISTINCT FROM NEW.thesis
     AND OLD.zone_source IS NOT DISTINCT FROM NEW.zone_source THEN
    RETURN NEW;
  END IF;
  NEW.updated_at := now();
  INSERT INTO entry_watch_history (ticker, status, zone_low, zone_high, invalidation, horizon, thesis, zone_source, changed_by)
  VALUES (NEW.ticker, NEW.status, NEW.zone_low, NEW.zone_high, NEW.invalidation, NEW.horizon, NEW.thesis, NEW.zone_source, actor);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;
