-- Fundamentals snapshot detail + versioned scores. Idempotent.
-- Apply: python scripts/migrate.py   (or psql -v ON_ERROR_STOP=1 -d stocks -f 014_fundamentals_value.sql)
SET search_path TO intel, public;

-- Finnhub stock/metric fields the 001 table had no home for. Ratios are fractions, margins are fractions.
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS pb               numeric(12,3);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS op_margin        numeric(8,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS net_margin       numeric(8,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS roe              numeric(8,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS debt_equity      numeric(10,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS current_ratio    numeric(10,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS beta             numeric(8,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS eps_growth_yoy   numeric(10,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS rev_growth_q_yoy numeric(10,4);
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS avg_vol_10d      bigint;
ALTER TABLE fundamentals ADD COLUMN IF NOT EXISTS updated_at       timestamptz NOT NULL DEFAULT now();

-- Which scoring code produced the row, and which inputs each component had.
ALTER TABLE scores_daily ADD COLUMN IF NOT EXISTS score_version text;
ALTER TABLE scores_daily ADD COLUMN IF NOT EXISTS coverage      jsonb;
UPDATE scores_daily SET score_version = 'v0' WHERE score_version IS NULL;
