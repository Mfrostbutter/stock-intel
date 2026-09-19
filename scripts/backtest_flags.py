#!/usr/bin/env python3
"""Backtest the price-only entry flags over historical indicators.

Reconstructs the three price-only flags from docs/SCORING.md over
indicators_daily history, then measures forward returns from prices_daily.
Sentiment/filing/insider-dependent flag terms are dropped (no history),
so this measures the price skeleton of each flag, not the full rule.

Flags reconstructed:
  pullback-in-uptrend: close > sma200, close < sma20, rsi14 in 35..45
  oversold-bounce:      rsi14 < 30, vol_z20 > 1.5
  breakout:             close > prior 20-day high, vol_z20 > 2

Read-only. Usage: python scripts/backtest_flags.py [horizon_days]
"""
import sys

import psycopg2

import secrets_env

HORIZON = int(sys.argv[1]) if len(sys.argv) > 1 else 20


SQL = f"""
WITH ind AS (
  SELECT i.ticker, i.as_of, i.sma20, i.sma200, i.rsi14, i.vol_z20, p.close, p.high
  FROM intel.indicators_daily i
  JOIN intel.prices_daily p ON p.ticker = i.ticker AND p.as_of = i.as_of
  WHERE i.sma200 IS NOT NULL AND i.rsi14 IS NOT NULL
    AND i.as_of <= (SELECT max(as_of) - {HORIZON} FROM intel.prices_daily)
),
hi20 AS (
  SELECT ticker, as_of, close, sma20, sma200, rsi14, vol_z20,
         max(high) OVER (PARTITION BY ticker ORDER BY as_of
                         ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS prior_hi20
  FROM ind
),
flagged AS (
  SELECT *, 'pullback-in-uptrend' AS flag FROM hi20
   WHERE close > sma200 AND close < sma20 AND rsi14 BETWEEN 35 AND 45
  UNION ALL
  SELECT *, 'oversold-bounce' FROM hi20 WHERE rsi14 < 30 AND vol_z20 > 1.5
  UNION ALL
  SELECT *, 'breakout' FROM hi20 WHERE prior_hi20 IS NOT NULL AND close > prior_hi20 AND vol_z20 > 2
),
scored AS (
  SELECT f.ticker, f.as_of, f.flag, f.close AS entry_close,
         (SELECT p2.close FROM intel.prices_daily p2
          WHERE p2.ticker = f.ticker AND p2.as_of >= f.as_of + {HORIZON}
          ORDER BY p2.as_of LIMIT 1) AS exit_close
  FROM flagged f
)
SELECT flag, count(*) AS n,
       round(avg((exit_close - entry_close) / entry_close) * 100, 2) AS avg_ret_pct,
       round(avg(CASE WHEN exit_close > entry_close THEN 1 ELSE 0 END) * 100, 1) AS win_rate_pct,
       round(min((exit_close - entry_close) / entry_close) * 100, 2) AS worst_pct,
       round(max((exit_close - entry_close) / entry_close) * 100, 2) AS best_pct
FROM scored
WHERE exit_close IS NOT NULL
GROUP BY flag
ORDER BY avg_ret_pct DESC
"""

BASELINE_SQL = f"""
WITH days AS (
  SELECT i.ticker, i.as_of, p.close AS entry_close,
         (SELECT p2.close FROM intel.prices_daily p2
          WHERE p2.ticker = i.ticker AND p2.as_of >= i.as_of + {HORIZON}
          ORDER BY p2.as_of LIMIT 1) AS exit_close
  FROM intel.indicators_daily i
  JOIN intel.prices_daily p ON p.ticker = i.ticker AND p.as_of = i.as_of
  WHERE i.sma200 IS NOT NULL AND i.rsi14 IS NOT NULL
    AND i.as_of <= (SELECT max(as_of) - {HORIZON} FROM intel.prices_daily)
)
SELECT round(avg((exit_close - entry_close) / entry_close) * 100, 2), count(*)
FROM days WHERE exit_close IS NOT NULL
"""


def main():
    conn = psycopg2.connect(**secrets_env.pg())
    with conn.cursor() as cur:
        print(f"Reconstructed price-only flags, {HORIZON}-day forward return:")
        print(f"{'flag':<24} {'n':>5} {'avg%':>8} {'win%':>6} {'worst%':>9} {'best%':>9}")
        cur.execute(SQL)
        for r in cur.fetchall():
            print(f"{r[0]:<24} {r[1]:>5} {r[2]:>8} {r[3]:>6} {r[4]:>9} {r[5]:>9}")

        cur.execute(BASELINE_SQL)
        row = cur.fetchone()
        print(f"\nBaseline (every eligible ticker-day): avg {row[0]}% over {row[1]} days")
    conn.close()


if __name__ == "__main__":
    main()