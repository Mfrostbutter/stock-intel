-- Stock Intel holdings. Database: stocks. Idempotent.
-- Apply: psql -v ON_ERROR_STOP=1 -d stocks -f 003_positions.sql
--
-- positions becomes the source of truth for "holding". The watchlist tag is derived by
-- trigger, so the pipeline's tags @> '{holding}' filters keep working untouched.

SET search_path TO intel, public;

-- An open position is a live row. Closing deletes the row and leaves a qty = 0 history row.
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'positions_qty_positive') THEN
    ALTER TABLE positions ADD CONSTRAINT positions_qty_positive CHECK (qty > 0);
  END IF;
END $$;

-- Append-only audit of every add, trim and close.
CREATE TABLE IF NOT EXISTS position_history (
  id          bigserial PRIMARY KEY,
  ticker      text NOT NULL,
  qty         numeric(18,6) NOT NULL,
  avg_cost    numeric(14,4),
  opened_at   date,
  notes       text,
  changed_at  timestamptz NOT NULL DEFAULT now(),
  changed_by  text NOT NULL DEFAULT 'ui'
);
CREATE INDEX IF NOT EXISTS position_history_ticker ON position_history (ticker, changed_at DESC);

-- Who made the change. The app sets stockintel.actor per connection; anything else reads 'ui'.
CREATE OR REPLACE FUNCTION log_position_change() RETURNS trigger AS $fn$
DECLARE
  actor text := COALESCE(NULLIF(current_setting('stockintel.actor', true), ''), 'ui');
BEGIN
  IF TG_OP = 'DELETE' THEN
    INSERT INTO position_history (ticker, qty, avg_cost, opened_at, notes, changed_by)
    VALUES (OLD.ticker, 0, OLD.avg_cost, OLD.opened_at, OLD.notes, actor);
    RETURN OLD;
  END IF;

  IF TG_OP = 'UPDATE'
     AND OLD.qty IS NOT DISTINCT FROM NEW.qty
     AND OLD.avg_cost IS NOT DISTINCT FROM NEW.avg_cost
     AND OLD.opened_at IS NOT DISTINCT FROM NEW.opened_at
     AND OLD.notes IS NOT DISTINCT FROM NEW.notes THEN
    RETURN NEW;
  END IF;

  INSERT INTO position_history (ticker, qty, avg_cost, opened_at, notes, changed_by)
  VALUES (NEW.ticker, NEW.qty, NEW.avg_cost, NEW.opened_at, NEW.notes, actor);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS position_history_trg ON positions;
CREATE TRIGGER position_history_trg AFTER INSERT OR UPDATE OR DELETE ON positions
  FOR EACH ROW EXECUTE FUNCTION log_position_change();

-- The 'holding' tag is derived, never hand-set. PATCH /api/watchlist strips it from input.
CREATE OR REPLACE FUNCTION sync_holding_tag() RETURNS trigger AS $fn$
BEGIN
  IF TG_OP = 'DELETE' THEN
    UPDATE watchlist SET tags = array_remove(tags, 'holding')
     WHERE ticker = OLD.ticker AND tags @> ARRAY['holding'];
    RETURN OLD;
  END IF;

  UPDATE watchlist SET tags = array_append(tags, 'holding')
   WHERE ticker = NEW.ticker AND NOT (tags @> ARRAY['holding']);
  RETURN NEW;
END;
$fn$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS positions_holding_tag_trg ON positions;
CREATE TRIGGER positions_holding_tag_trg AFTER INSERT OR UPDATE OR DELETE ON positions
  FOR EACH ROW EXECUTE FUNCTION sync_holding_tag();

-- Reconcile any tag drift from before the trigger existed.
UPDATE watchlist w SET tags = array_append(w.tags, 'holding')
 WHERE EXISTS (SELECT 1 FROM positions p WHERE p.ticker = w.ticker)
   AND NOT (w.tags @> ARRAY['holding']);
UPDATE watchlist w SET tags = array_remove(w.tags, 'holding')
 WHERE w.tags @> ARRAY['holding']
   AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.ticker = w.ticker);

-- Share counts and dollar values stay out of the emailed brief unless this is true.
INSERT INTO config (key, value) VALUES ('email_position_values', 'false'::jsonb)
ON CONFLICT (key) DO NOTHING;

-- New table and sequence need their own grants; 002's ALL TABLES ran before they existed.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_app') THEN
    GRANT SELECT, INSERT, UPDATE, DELETE ON position_history, positions TO stocks_app;
    GRANT USAGE, SELECT ON SEQUENCE position_history_id_seq TO stocks_app;
  END IF;
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    GRANT SELECT ON position_history, positions TO stocks_n8n;
    GRANT USAGE, SELECT ON SEQUENCE position_history_id_seq TO stocks_n8n;
  END IF;
END $$;
