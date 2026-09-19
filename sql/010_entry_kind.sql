-- Stock Intel entry watch: holdings as add-on-dip rows. Database: stocks. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 010_entry_kind.sql)
--
-- kind = new opens a position; kind = add lowers the average on a holding, so its zone must sit
-- below the cost basis. Open positions enrol as add rows when entry_watch_holdings is true.

SET search_path TO intel, public;

ALTER TABLE entry_watch         ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'new';   -- new | add
ALTER TABLE entry_watch_history ADD COLUMN IF NOT EXISTS kind text;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'entry_watch_kind') THEN
    ALTER TABLE entry_watch ADD CONSTRAINT entry_watch_kind CHECK (kind IN ('new', 'add'));
  END IF;
END $$;

-- Rows that already hold a position are add rows.
UPDATE entry_watch e SET kind = 'add'
 WHERE kind = 'new' AND EXISTS (SELECT 1 FROM positions p WHERE p.ticker = e.ticker);

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
     AND OLD.zone_source IS NOT DISTINCT FROM NEW.zone_source
     AND OLD.kind IS NOT DISTINCT FROM NEW.kind THEN
    RETURN NEW;
  END IF;
  NEW.updated_at := now();
  INSERT INTO entry_watch_history (ticker, status, zone_low, zone_high, invalidation, horizon, thesis, zone_source, kind, changed_by)
  VALUES (NEW.ticker, NEW.status, NEW.zone_low, NEW.zone_high, NEW.invalidation, NEW.horizon, NEW.thesis, NEW.zone_source, NEW.kind, actor);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

INSERT INTO config (key, value) VALUES
  ('entry_watch_holdings',   'true'::jsonb),    -- enrol every open position as an add row
  ('entry_dca_min_discount', '0.02'::jsonb)     -- an add zone tops out this far below the average cost
ON CONFLICT (key) DO NOTHING;
