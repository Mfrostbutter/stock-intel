"""Session clock in America/New_York, off the Alpaca paper clock."""
from datetime import datetime
from zoneinfo import ZoneInfo

from . import alpaca_data

NY = ZoneInfo("America/New_York")


def now_et():
    return datetime.now(NY)


def trade_date():
    return now_et().date().isoformat()


def market_state():
    """(is_open, next_open, next_close) from the broker clock. Startup uses this + the
    paper host to confirm we are on the paper environment, never live."""
    c = alpaca_data.clock()
    return bool(c.get("is_open")), c.get("next_open"), c.get("next_close")
