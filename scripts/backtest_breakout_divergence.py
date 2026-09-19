#!/usr/bin/env python3
"""Phase-4 experiment: does an RSI-divergence split rescue the `breakout` flag?

The phase-4 pre-check (backtest_flags.py) found `breakout` robustly negative at every horizon.
Elliott Wave frames a new-high breakout that carries bearish RSI divergence as a wave-5
exhaustion, not a continuation. This splits the price-skeleton breakout into divergent vs clean
and measures forward returns at several horizons, against the all-breakout and all-days baselines.

Breakout (price skeleton, matches backtest_flags.py): close > prior 20-day high AND vol_z20 > 2.
Divergence proxy (deterministic): rsi14 at the breakout bar < max(rsi14) over the prior 20 bars,
  i.e. momentum at the new high is below recent peak momentum. A proxy for "price higher high,
  RSI lower high". clean = rsi14 >= that prior-window rsi max.

Read-only. Usage: python scripts/backtest_breakout_divergence.py [horizon ...]  (default 5 10 20 60)
"""
import sys

import psycopg2

import secrets_env

HORIZONS = [int(x) for x in sys.argv[1:]] or [5, 10, 20, 60]


def split_sql(h):
    return f"""
WITH ind AS (
  SELECT i.ticker, i.as_of, i.rsi14, i.vol_z20, p.close, p.high
  FROM intel.indicators_daily i
  JOIN intel.prices_daily p ON p.ticker = i.ticker AND p.as_of = i.as_of
  WHERE i.rsi14 IS NOT NULL AND i.vol_z20 IS NOT NULL
    AND i.as_of <= (SELECT max(as_of) - {h} FROM intel.prices_daily)
),
w AS (
  SELECT *,
    max(high)  OVER (PARTITION BY ticker ORDER BY as_of ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_hi20,
    max(rsi14) OVER (PARTITION BY ticker ORDER BY as_of ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_rsi_max
  FROM ind
),
bo AS (
  SELECT ticker, as_of, close AS entry_close,
         CASE WHEN rsi14 < prior_rsi_max THEN 'breakout-divergent' ELSE 'breakout-clean' END AS grp
  FROM w
  WHERE prior_hi20 IS NOT NULL AND prior_rsi_max IS NOT NULL AND close > prior_hi20 AND vol_z20 > 2
),
scored AS (
  SELECT b.grp, b.entry_close,
         (SELECT p2.close FROM intel.prices_daily p2
          WHERE p2.ticker = b.ticker AND p2.as_of >= b.as_of + {h}
          ORDER BY p2.as_of LIMIT 1) AS exit_close
  FROM bo b
)
SELECT coalesce(grp, 'breakout-ALL') AS grp, count(*) AS n,
       round(avg((exit_close - entry_close) / entry_close) * 100, 2) AS avg_ret,
       round(avg(CASE WHEN exit_close > entry_close THEN 1 ELSE 0 END) * 100, 1) AS win,
       round(min((exit_close - entry_close) / entry_close) * 100, 2) AS worst,
       round(max((exit_close - entry_close) / entry_close) * 100, 2) AS best
FROM scored
WHERE exit_close IS NOT NULL
GROUP BY GROUPING SETS ((grp), ())
ORDER BY grp
"""


def baseline_sql(h):
    return f"""
WITH days AS (
  SELECT i.ticker, i.as_of, p.close AS entry_close,
         (SELECT p2.close FROM intel.prices_daily p2
          WHERE p2.ticker = i.ticker AND p2.as_of >= i.as_of + {h}
          ORDER BY p2.as_of LIMIT 1) AS exit_close
  FROM intel.indicators_daily i
  JOIN intel.prices_daily p ON p.ticker = i.ticker AND p.as_of = i.as_of
  WHERE i.rsi14 IS NOT NULL
    AND i.as_of <= (SELECT max(as_of) - {h} FROM intel.prices_daily)
)
SELECT round(avg((exit_close - entry_close) / entry_close) * 100, 2), count(*)
FROM days WHERE exit_close IS NOT NULL
"""


def main():
    conn = psycopg2.connect(**secrets_env.pg())
    with conn.cursor() as cur:
        for h in HORIZONS:
            print(f"\n=== {h}-day forward return ===")
            print(f"{'group':<20} {'n':>5} {'avg%':>8} {'win%':>6} {'worst%':>9} {'best%':>9}")
            cur.execute(split_sql(h))
            for r in cur.fetchall():
                print(f"{r[0]:<20} {r[1]:>5} {r[2]:>8} {r[3]:>6} {r[4]:>9} {r[5]:>9}")
            cur.execute(baseline_sql(h))
            b = cur.fetchone()
            print(f"{'baseline (all days)':<20} {b[1]:>5} {b[0]:>8}")
    conn.close()


if __name__ == "__main__":
    main()
