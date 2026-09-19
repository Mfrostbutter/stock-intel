"""Entry watch rule layer. Pure, no DB. Run: python -m pytest tests/test_entries_rules.py -q"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.entries import rule_signal, rsi_band, signal_rank, trend_of, zone_distance  # noqa: E402


def base(**kw):
    row = {"close": 100.0, "zone_low": 90.0, "zone_high": 95.0, "invalidation": 80.0,
           "sma50": 96.0, "sma200": 85.0, "rsi14": 50.0, "vol_z20": 0.3, "news_score": 0.1, "flags": [], "risks": []}
    row.update(kw)
    return row


def test_in_zone_is_enter():
    r = rule_signal(base(close=92.0))
    assert r["signal"] == "enter" and r["in_zone"] and r["distance_pct"] == 0.0


def test_near_within_three_percent_above_or_below():
    assert rule_signal(base(close=97.0))["signal"] == "near"          # 2.1% above the zone
    assert rule_signal(base(close=88.5))["signal"] == "near"          # 1.7% below the zone
    assert rule_signal(base(close=100.0))["signal"] == "wait"         # 5% above


def test_below_invalidation_is_avoid_even_inside_nothing_else():
    r = rule_signal(base(close=79.0, zone_low=70.0, zone_high=85.0))
    assert r["signal"] == "avoid" and r["below_invalidation"]


def test_risk_flag_blocks_enter():
    assert rule_signal(base(close=92.0, risks=["earnings-5d"]))["signal"] == "avoid"
    assert rule_signal(base(close=97.0, risks=["earnings-5d"]))["signal"] == "wait"


def test_no_zone_falls_back_to_flags():
    assert rule_signal(base(zone_low=None, zone_high=None, flags=["pullback-in-uptrend"]))["signal"] == "near"
    assert rule_signal(base(zone_low=None, zone_high=None))["signal"] == "wait"


def test_missing_price_is_wait_not_crash():
    r = rule_signal({"zone_low": 1, "zone_high": 2})
    assert r["signal"] == "wait" and r["trend"] == "unknown" and "no stored price" in r["reasons"]


def test_live_price_overrides_close():
    r = rule_signal(base(close=100.0, price=92.0))
    assert r["signal"] == "enter" and r["price"] == 92.0


def test_near_pct_is_configurable():
    assert rule_signal(base(close=97.0), near_pct=0.01)["signal"] == "wait"


def test_helpers():
    assert trend_of(100, 90, 80) == "up" and trend_of(70, 90, 80) == "down" and trend_of(85, 90, 80) == "mixed"
    assert trend_of(None, 1, 2) == "unknown"
    assert rsi_band(25) == "oversold" and rsi_band(40) == "low" and rsi_band(55) == "neutral" and rsi_band(75) == "overbought"
    assert zone_distance(110, 90, 100) == (100 * 0 + (110 - 100) / 110, False)
    assert zone_distance(95, 90, 100) == (0.0, True)
    assert zone_distance(100, None, None) == (None, False)
    assert [signal_rank(s) for s in ("enter", "near", "wait", "avoid", "x")] == [0, 1, 2, 3, 4]


def test_add_row_above_cost_is_never_enter_or_near():
    # In zone and 5% above the average cost: adding would raise it, so wait.
    r = rule_signal(base(close=92.0, kind="add", avg_cost=87.0))
    assert r["signal"] == "wait" and r["kind"] == "add" and r["vs_cost_pct"] > 0
    assert any("above the average cost" in x for x in r["reasons"])
    # Same row below the cost basis enters, and the discount shows in the reasons.
    r = rule_signal(base(close=92.0, kind="add", avg_cost=100.0))
    assert r["signal"] == "enter" and r["vs_cost_pct"] == -0.08
    assert any("below the average cost" in x for x in r["reasons"])
    # Invalidation still wins over the cost read.
    assert rule_signal(base(close=79.0, zone_low=70.0, zone_high=85.0, kind="add", avg_cost=100.0))["signal"] == "avoid"
    # A new row ignores avg_cost even when it is present.
    assert rule_signal(base(close=92.0, kind="new", avg_cost=87.0))["signal"] == "enter"


def test_add_zone_cap():
    from decimal import Decimal
    from stockintel.entries import add_zone_cap
    assert add_zone_cap(100.0) == 98.0
    assert add_zone_cap(Decimal("929.94"), 0.02) == 911.3412
    assert add_zone_cap(None) is None and add_zone_cap(0) is None
