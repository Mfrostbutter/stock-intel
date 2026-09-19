"""Runtime config for the stock-intel app. Every value comes from the environment."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(APP_ROOT / ".env")
load_dotenv(APP_ROOT.parent / ".env")  # repo-root .env is the deploy default

VERSION = "0.4.0"
STATIC_DIR = APP_ROOT / "static"

APP_TOKEN = os.environ.get("STOCK_INTEL_APP_TOKEN", "").strip()

PG_HOST = os.environ.get("STOCKS_PG_HOST", "postgres")
PG_PORT = int(os.environ.get("STOCKS_PG_PORT", "5432"))
PG_DB   = os.environ.get("STOCKS_PG_DB", "stocks")
PG_USER = os.environ.get("STOCKS_PG_USER", "stocks_app")
PG_PASSWORD = os.environ.get("STOCKS_APP_PG_PASSWORD", "")
# Read-only role the analyst tools query as. Empty means the app pool with a read-only transaction.
PG_ANALYST_USER = os.environ.get("STOCKS_ANALYST_PG_USER", "stocks_analyst")
PG_ANALYST_PASSWORD = os.environ.get("STOCKS_ANALYST_PG_PASSWORD", "").strip()

N8N_BASE_URL = os.environ.get("N8N_BASE_URL", "http://n8n:5678").rstrip("/")
N8N_RUN_WEBHOOK_PATH = os.environ.get("N8N_RUN_WEBHOOK_PATH", "stock-intel/run").strip("/")
N8N_BACKFILL_WEBHOOK_PATH = os.environ.get("N8N_BACKFILL_WEBHOOK_PATH", "stock-intel/backfill").strip("/")
N8N_WEBHOOK_SECRET = os.environ.get("STOCK_INTEL_N8N_WEBHOOK_SECRET", "").strip()
N8N_WEBHOOK_HEADER = "X-Stock-Intel-Secret"

FINNHUB_API_KEY = os.environ.get("FINNHUB_API_KEY", "").strip()

# Live quotes. Free tier is the IEX feed; "sip" needs a paid Alpaca plan.
ALPACA_KEY_ID = os.environ.get("ALPACA_API_KEY_ID", "").strip()
ALPACA_SECRET_KEY = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
QUOTE_FEED = os.environ.get("STOCK_INTEL_QUOTE_FEED", "iex").strip()
QUOTE_CACHE_SECONDS = int(os.environ.get("STOCK_INTEL_QUOTE_CACHE_SECONDS", "20"))


def _alias(new: str, old: str) -> str:
    """Accept either spelling of a key variable and export both, so a provider row that
    still names the old one keeps working."""
    v = (os.environ.get(new) or os.environ.get(old) or "").strip()
    if v:
        os.environ.setdefault(new, v)
        os.environ.setdefault(old, v)
    return v


ANTHROPIC_API_KEY = _alias("ANTHROPIC_API_KEY", "STOCK_INTEL_ANTHROPIC_KEY")
OPENROUTER_API_KEY = _alias("OPENROUTER_API_KEY", "OPEN_ROUTER_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "").strip()
OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "").strip().lower()

TZ = os.environ.get("TZ", "America/New_York")

HTTP_TIMEOUT = 15
