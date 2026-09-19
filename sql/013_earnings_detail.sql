-- Earnings calendar detail. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 013_earnings_detail.sql)
SET search_path TO intel, public;

ALTER TABLE earnings_calendar ADD COLUMN IF NOT EXISTS hour           text;         -- bmo | amc | dmh | null
ALTER TABLE earnings_calendar ADD COLUMN IF NOT EXISTS fiscal_year    int;
ALTER TABLE earnings_calendar ADD COLUMN IF NOT EXISTS fiscal_quarter int;
ALTER TABLE earnings_calendar ADD COLUMN IF NOT EXISTS source         text;
ALTER TABLE earnings_calendar ADD COLUMN IF NOT EXISTS updated_at     timestamptz NOT NULL DEFAULT now();

CREATE INDEX IF NOT EXISTS earnings_date ON earnings_calendar (report_date);
