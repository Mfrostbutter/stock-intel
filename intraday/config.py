"""Recorder config. Every value comes from the environment; secrets are never printed."""
import functools
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file():
    path = Path(os.environ.get("STOCK_INTEL_ENV_FILE", REPO_ROOT / ".env"))
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(path)


_load_env_file()


@functools.lru_cache(maxsize=16)
def secret(name):
    v = os.environ.get(name)
    if not v:
        raise SystemExit(f"{name} is not set (container env or .env at the repo root)")
    return v.strip()


# DB: the intraday service role. Writes intraday.*, no access to intel writes (proven in sql/011).
DB = dict(host=os.environ.get("STOCKS_PG_HOST", "postgres"),
          port=int(os.environ.get("STOCKS_PG_PORT", "5432")),
          dbname=os.environ.get("STOCKS_PG_DB", "stocks"),
          user="stocks_intraday_svc",
          options="-c search_path=intraday,intel,public", connect_timeout=10)

FEED = os.environ.get("SI_INTRADAY_FEED", "iex")           # engineering mode; sip needs the paid plan
SOURCE = f"alpaca_{FEED}"
SYMBOLS = [s.strip().upper() for s in os.environ.get(
    "SI_INTRADAY_SYMBOLS", "SPY,QQQ").split(",") if s.strip()]
POLL_SECONDS = int(os.environ.get("SI_INTRADAY_POLL_SECONDS", "60"))
LEASE_SECONDS = int(os.environ.get("SI_INTRADAY_LEASE_SECONDS", "120"))

ALPACA_DATA = "https://data.alpaca.markets"
ALPACA_PAPER = "https://paper-api.alpaca.markets"  # clock/calendar only; startup asserts this is paper
