-- Insider transaction detail + rec source for the Collect-Signals sub-workflow. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 015_signals.sql)
SET search_path TO intel, public;

-- Finnhub carries the SEC transaction code and whether the row is a derivative; tx_type keeps the coarse P / S / other.
ALTER TABLE insider_tx ADD COLUMN IF NOT EXISTS tx_date    date;
ALTER TABLE insider_tx ADD COLUMN IF NOT EXISTS code       text;
ALTER TABLE insider_tx ADD COLUMN IF NOT EXISTS derivative boolean;
ALTER TABLE insider_tx ADD COLUMN IF NOT EXISTS source     text;
ALTER TABLE insider_tx ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE analyst_recs ADD COLUMN IF NOT EXISTS source     text;
ALTER TABLE analyst_recs ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();
