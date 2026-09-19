-- Intraday fork: n8n (stocks_n8n) grants on the intraday schema. Database: stocks.
-- Apply as postgres: psql -v ON_ERROR_STOP=1 -U postgres -d stocks -f 012_intraday_n8n_grants.sql
--
-- Deferred from sql/011 (see its closing comment). WF-01/07/08 run as role stocks_n8n and write
-- the recorder, context and operational tables only.
-- The money and decision ledgers stay execution-controller-owned; n8n reads them for reports.
-- Append-only discipline: no DELETE.

SET search_path TO intraday, intel, public;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stocks_n8n') THEN
    RAISE EXCEPTION 'role stocks_n8n missing';
  END IF;

  -- Schema + sequences (bigserial ids on the write-set tables).
  GRANT USAGE ON SCHEMA intraday TO stocks_n8n;
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA intraday TO stocks_n8n;

  -- Write set: recorder/context/operational tables. SELECT+INSERT+UPDATE, no DELETE
  -- (corrections append as new rows; history is never overwritten).
  --   session_manifests  WF-01 commits + freezes the day's manifest
  --   source_events      WF-01 movers snapshots; WF-02/03/04 news/sec/calendar events
  --   bar_versions       WF-08 appends gap-repair bars as new versions
  --   calendar_versions  WF-04 calendar snapshots (WF-01 reads)
  --   ingestion_jobs     WF-01/08 job lease + fencing + cursor_state
  --   source_cursors     committed per-source cursor advance
  --   source_health      WF-07 collection-health heartbeats
  --   report_versions    WF-09 provisional/reconciled report artifacts
  --   incidents          WF-07 deduplicated collection-health incidents (kind=stale_feed, ...)
  GRANT SELECT, INSERT, UPDATE ON
    intraday.session_manifests, intraday.source_events, intraday.bar_versions,
    intraday.calendar_versions, intraday.ingestion_jobs, intraday.source_cursors,
    intraday.source_health, intraday.report_versions, intraday.incidents
  TO stocks_n8n;

  -- Read set: feature outputs + money/decision ledgers. SELECT only.
  -- n8n never writes the trading ledgers; WF-06 reads feature_batches, WF-09 reads ledgers.
  GRANT SELECT ON
    intraday.feature_batches, intraday.sessions, intraday.cash_events,
    intraday.strategy_versions, intraday.input_events, intraday.decisions,
    intraday.risk_checks, intraday.order_events, intraday.fills,
    intraday.position_snapshots, intraday.experiment_runs, intraday.daily_metrics
  TO stocks_n8n;
END $$;
