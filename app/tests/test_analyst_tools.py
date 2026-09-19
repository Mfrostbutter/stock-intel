"""Guarded SQL, safe calc and the deterministic evidence checks. No DB."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.analyst.evidence import grounded, levels_for, validate_draft  # noqa: E402
from stockintel.analyst.tools import SqlRejected, guard_select, safe_calc  # noqa: E402


@pytest.mark.parametrize("bad", [
    "DROP TABLE watchlist", "DELETE FROM positions", "SELECT 1; SELECT 2", "UPDATE config SET value = '1'",
    "SELECT * FROM pg_catalog.pg_roles", "SELECT * FROM information_schema.tables",
    "SELECT current_setting('x')", "SELECT pg_sleep(10)", "SELECT 1 -- hi", "COPY watchlist TO '/tmp/x'",
    "select * from other_db.intel.watchlist", "", "WITH x AS (DELETE FROM positions RETURNING *) SELECT * FROM x",
])
def test_guard_rejects(bad):
    with pytest.raises(SqlRejected):
        guard_select(bad)


def test_guard_forces_limit():
    assert guard_select("SELECT ticker FROM watchlist").endswith("LIMIT 500")
    assert guard_select("SELECT ticker FROM watchlist LIMIT 10;").endswith("LIMIT 10")
    assert guard_select("SELECT ticker FROM watchlist LIMIT 99999").endswith("LIMIT 500")
    assert guard_select("with x as (select 1 as a) select a from x offset 2").endswith("LIMIT 500")


def test_safe_calc():
    assert safe_calc("(228 - 219.32) / 219.32") == pytest.approx(0.03958, abs=1e-4)
    assert safe_calc("abs(-3) + round(2.567, 1) + max(1, 2) ** 2") == pytest.approx(9.6)
    for bad in ("__import__('os')", "1 if 1 else 2", "x + 1", "2 ** 1000", "open('f')"):
        with pytest.raises((ValueError, SyntaxError)):
            safe_calc(bad)


EV = {
    "db:prices:NVDA:d": {"id": "db:prices:NVDA:d", "kind": "db", "ref": "x", "excerpt": "", "ticker": "NVDA",
                         "payload": {"latest": {"close": 219.32, "sma20": 228.0, "sma200": 180.0, "rsi14": 41.0}}},
    "db:scores:INTC:d": {"id": "db:scores:INTC:d", "kind": "db", "ref": "x", "excerpt": "", "ticker": "INTC",
                         "payload": {"close": 88.93, "rsi14": 28.0}},
}


def test_levels_ignore_non_level_fields():
    lv = levels_for(EV, "NVDA", ["db:prices:NVDA:d"])
    assert 219.32 in lv and 228.0 in lv and 41.0 not in lv
    assert grounded(230.0, lv) and not grounded(300.0, lv)


def test_validate_draft_strips_and_flags():
    draft = {
        "market_read": [{"text": "ok", "cites": ["db:prices:NVDA:d"], "levels": []},
                        {"text": "bogus", "cites": ["web:1"], "levels": []}],
        "holdings": [{"ticker": "nvda", "stance": "hold", "confidence": 0.5,
                      "rationale": [{"text": "near sma20", "cites": ["db:prices:NVDA:d"], "levels": [230.0]}],
                      "invalidation": {"text": "below 100", "cites": ["db:prices:NVDA:d"], "levels": [100.0]}}],
        "entries": [{"ticker": "AAPL", "setup": "x", "entry_zone": {"low": 1, "high": 2},
                     "invalidation": {"text": "x", "cites": [], "levels": []}, "horizon": "days", "confidence": 0.1,
                     "rationale": [{"text": "y", "cites": [], "levels": []}]}],
        "avoid": [{"ticker": "INTC", "reason": {"text": "collapse", "cites": ["db:scores:INTC:d"], "levels": []}}],
        "questions": [], "data_gaps": [],
    }
    clean, rep = validate_draft(draft, EV, ["NVDA", "INTC"], critic_unsupported=["collapse"])
    assert len(clean["market_read"]) == 1
    assert clean["holdings"][0]["ticker"] == "NVDA"
    assert clean["holdings"][0]["invalidation"].get("ungrounded") == [100.0]
    assert clean["avoid"] == []                       # critic struck it
    assert rep["dropped_tickers"] == ["AAPL"]
    assert rep["stripped"] == 2 and rep["ungrounded"] == 1 and rep["claims"] == 5
    assert any("unknown citation web:1" in i for i in rep["issues"])


def test_invalidation_prefix_is_stripped():
    from stockintel.analyst.evidence import strip_invalidation_prefix
    c = {"text": "Invalidation: a close below the 50-day average at 135.99.", "cites": ["x"], "levels": [135.99]}
    assert strip_invalidation_prefix(c)["text"] == "A close below the 50-day average at 135.99."
    c2 = {"text": "Invalidation is a close back below 100.13.", "cites": ["x"], "levels": [100.13]}
    assert strip_invalidation_prefix(c2)["text"] == "A close back below 100.13."
    plain = {"text": "A close below 200.", "cites": ["x"], "levels": [200.0]}
    assert strip_invalidation_prefix(plain) is plain
    assert strip_invalidation_prefix(None) is None
