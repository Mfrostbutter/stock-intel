"""intraday.* writer (role stocks_intraday_svc). Idempotent upserts, single-writer lease."""
import hashlib
import json

import psycopg2
from psycopg2.extras import execute_values

from . import config


def connect():
    return psycopg2.connect(password=config.secret("STOCKS_INTRADAY_SVC_PASSWORD"), **config.DB)


def payload_hash(d):
    return hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()


def claim_lease(conn, session_id, due_window, owner, token):
    """Single-writer claim. Succeeds only if no live lease is held by another owner.
    Returns True when this owner holds the lease. Fencing: higher token wins."""
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO intraday.ingestion_jobs
              (workflow, source, session_id, due_window, status, lease_owner, fencing_token, lease_expires, claimed_at)
            VALUES ('recorder', %s, %s, %s, 'claimed', %s, %s, now() + (%s || ' seconds')::interval, now())
            ON CONFLICT (workflow, source, session_id, due_window) DO UPDATE
              SET lease_owner = EXCLUDED.lease_owner, fencing_token = EXCLUDED.fencing_token,
                  lease_expires = EXCLUDED.lease_expires, status = 'claimed',
                  attempts = intraday.ingestion_jobs.attempts + 1
              WHERE intraday.ingestion_jobs.lease_expires < now()
                 OR intraday.ingestion_jobs.lease_owner = EXCLUDED.lease_owner
            RETURNING fencing_token
        """, (config.SOURCE, session_id, due_window, owner, token, config.LEASE_SECONDS))
        held = cur.fetchone() is not None
    conn.commit()
    return held


def renew_lease(conn, session_id, due_window, owner, token):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE intraday.ingestion_jobs
               SET lease_expires = now() + (%s || ' seconds')::interval
             WHERE workflow = 'recorder' AND source = %s AND session_id = %s AND due_window = %s
               AND lease_owner = %s AND fencing_token = %s
            RETURNING 1
        """, (config.LEASE_SECONDS, config.SOURCE, session_id, due_window, owner, token))
        ok = cur.fetchone() is not None
    conn.commit()
    return ok


def write_bars(conn, rows):
    """rows: dicts of one completed 1-min bar each. Idempotent on (symbol, bar_end, bar_interval, version)."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        res = execute_values(cur, """
            INSERT INTO intraday.bar_versions
              (session_id, symbol, bar_end, open, high, low, close, volume, vwap, trade_count,
               feed, available_at, received_at, payload_hash)
            VALUES %s
            ON CONFLICT (symbol, bar_end, bar_interval, version) DO NOTHING
            RETURNING 1
        """, rows, template=("(%(session_id)s,%(symbol)s,%(bar_end)s,%(open)s,%(high)s,%(low)s,%(close)s,"
                             "%(volume)s,%(vwap)s,%(trade_count)s,%(feed)s,%(available_at)s,%(received_at)s,%(payload_hash)s)"),
                             fetch=True)
        n = len(res)
    conn.commit()
    return n


def write_quotes(conn, rows):
    """rows: quote snapshots -> source_events. Dedup on (source, source_version, payload_hash)."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        res = execute_values(cur, """
            INSERT INTO intraday.source_events
              (session_id, source, event_type, symbol, event_at, available_at, received_at,
               feed, completeness, payload_hash, payload)
            VALUES %s
            ON CONFLICT (source, source_version, payload_hash) DO NOTHING
            RETURNING 1
        """, rows, template=("(%(session_id)s,%(source)s,'quote',%(symbol)s,%(event_at)s,%(available_at)s,"
                             "%(received_at)s,%(feed)s,'complete',%(payload_hash)s,%(payload)s)"),
                             fetch=True)
        n = len(res)
    conn.commit()
    return n


def heartbeat(conn, provider_age_s, duration_ms, missing_symbols, http_failures, status):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO intraday.source_health
              (source, checked_at, last_success_at, provider_age_s, duration_ms, missing_symbols, http_failures, status)
            VALUES (%s, now(), now(), %s, %s, %s, %s, %s)
        """, (config.SOURCE, provider_age_s, duration_ms, missing_symbols, http_failures, status))
    conn.commit()
