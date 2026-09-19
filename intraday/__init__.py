"""Stock-intel intraday recorder (Increment 3 foundation).

Read-only market-data recorder for the intraday fork: it records completed 1-min bars
and quote snapshots into the intraday.* schema with point-in-time timestamps, under a
single-writer lease. No orders, no trading, no broker mutation. See docs/ intraday specs.
"""
