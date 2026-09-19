"""CLI: python -m intraday [--once] [--warmup-min N]. Read-only recorder, no orders."""
import argparse

from . import recorder

p = argparse.ArgumentParser(prog="intraday", description="Read-only intraday market recorder (no orders, no trading).")
p.add_argument("--once", action="store_true", help="run one poll then exit (smoke test)")
p.add_argument("--warmup-min", type=int, default=30, help="minutes of completed bars to backfill on start")
a = p.parse_args()
recorder.run(once=a.once, warmup_minutes=a.warmup_min)
