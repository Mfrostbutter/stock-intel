"""Headless entry-signal pass: the analyst graph under ENTRY_PROFILE with stub models.

Proves the entry_watch baseline item with its rule read, the fixed research set, no plan call,
the EntrySignalDraft checks (citations, levels, dropped tickers), and the markdown.
Run: python -m pytest tests/test_entry_graph.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.analyst.entry import ENTRY_PROFILE, validate_signals  # noqa: E402
from stockintel.analyst.graph import build_analyst_graph  # noqa: E402
from stockintel.analyst.schemas import Claim, EntrySignalDraft, EntrySignalView, EntryZone  # noqa: E402
from stockintel.analyst.tools import SQL, ToolSet  # noqa: E402
from tests.test_analyst_graph import AS_OF, ROWS, StubModel  # noqa: E402

ENTRY_ROWS = [
    {"ticker": "INTC", "name": "Intel", "thesis": "foundry turn", "zone_low": 85.0, "zone_high": 90.0,
     "invalidation": 75.0, "horizon": "weeks", "status": "watching", "notes": None, "added_at": None,
     "close": 88.93, "price_as_of": AS_OF, "sma20": 95.0, "sma50": 100.0, "sma200": 70.0, "rsi14": 28.0,
     "vol_z20": 2.1, "hi52w": 110.0, "lo52w": 30.0, "ret_5d": -0.1, "ret_20d": -0.2, "news_score": -0.05,
     "news_count": 3, "composite": 34.0, "flags": [], "risks": ["sentiment-collapse"]},
]


def fake_query(sql: str, params: dict) -> list[dict]:
    if sql == SQL["entry_watch"]:
        return list(ENTRY_ROWS)
    for name, tmpl in SQL.items():
        if sql == tmpl:
            return list(ROWS.get(name, []))
    return []


def good_signals() -> EntrySignalDraft:
    return EntrySignalDraft(signals=[EntrySignalView(
        ticker="INTC", signal="wait", confidence=0.4, summary="Inside the zone but a risk flag is live.",
        rationale=[Claim(text="Close 88.93 sits inside the 85 to 90 zone.", cites=["db:entry:INTC"], levels=[88.93, 85.0, 90.0]),
                   Claim(text="RSI 28 with a sentiment-collapse risk flag.", cites=[f"db:scores:INTC:{AS_OF}"])],
        invalidation=Claim(text="A close below 75.", cites=["db:entry:INTC"], levels=[75.0]),
        zone_check="holds", watch_for=["risk flag clearing"])],
        data_gaps=["no fundamentals stored"])


def bad_signals() -> EntrySignalDraft:
    return EntrySignalDraft(signals=[
        EntrySignalView(ticker="INTC", signal="enter", confidence=0.9, summary="x",
                        rationale=[Claim(text="Made up.", cites=["web:1"])],
                        invalidation=Claim(text="y", cites=[]), zone_check="unclear"),
        EntrySignalView(ticker="TSLA", signal="enter", confidence=0.9, summary="x",
                        rationale=[Claim(text="Off list.", cites=["db:entry:INTC"])],
                        invalidation=Claim(text="y", cites=[]), zone_check="unclear"),
    ])


def _run(model, focus=("INTC",)):
    persisted: list[dict] = []
    tools = ToolSet(fake_query, AS_OF)
    effects = {"load_brief": lambda d: None, "hitl_enabled": lambda: False,
               "persist": lambda p: persisted.append(p) or 9}
    prompts = {"system": "sys", "plan": "{as_of}", "gather": "unused", "draft": "unused", "critique": "{draft} {index}",
               "entry_gather": "{as_of} {tickers} {claims} {focus} {question}",
               "entry_draft": "{as_of} {question} {tickers} {index} {focus}"}
    g = build_analyst_graph(model, tools, effects, prompts, critic=model, profile=ENTRY_PROFILE)
    out = g.invoke({"as_of": AS_OF, "mode": "entry", "question": "entry signals", "focus_tickers": list(focus)},
                   {"recursion_limit": 40})
    return out, persisted


def test_entry_pass_persists_signals_with_rule_evidence():
    out, persisted = _run(StubModel([good_signals()], tool_turns=[]))
    assert out["status"] == "ok"
    p = persisted[0]
    assert p["mode"] == "entry" and p["status"] == "ok"
    ev = p["evidence"]
    assert ev["db:entry:INTC"]["payload"]["rule"]["signal"] == "avoid"      # in zone but a risk flag is live
    assert "rule=avoid" in ev["db:entry:INTC"]["excerpt"]
    assert f"db:prices:INTC:{AS_OF}" in ev                                    # prefetch ran for the focus ticker
    assert f"db:prices:NVDA:{AS_OF}" not in ev                                # and only for it
    sig = p["analysis"]["signals"][0]
    assert sig["ticker"] == "INTC" and sig["signal"] == "wait"
    assert sig["rationale"][0].get("ungrounded") is None                      # 85/90/88.93 are cited levels
    assert p["report"]["stripped"] == 0 and p["report"]["claims"] == 3
    assert "**INTC**: wait" in p["markdown"] and "Watch: risk flag clearing" in p["markdown"]
    assert p["plan"]["research"] == ["INTC"] and p["plan"]["source"] == "code"


def test_no_plan_model_call_in_entry_mode():
    m = StubModel([good_signals()], tool_turns=[])
    m.plan = None                                     # would raise if the plan step called the model
    _, persisted = _run(m)
    assert persisted[0]["status"] == "ok"
    assert not any(i.startswith("plan notes failed") for i in persisted[0]["report"]["issues"])


def test_focus_outside_watchlist_is_ignored():
    out, persisted = _run(StubModel([good_signals()], tool_turns=[]), focus=("INTC", "ZZZZ"))
    assert out["plan"]["research"] == ["INTC"]


def test_bad_signals_retry_then_invalid():
    out, persisted = _run(StubModel([bad_signals(), bad_signals()], tool_turns=[]))
    assert out["attempt"] == 2 and persisted[0]["status"] == "invalid"
    rep = persisted[0]["report"]
    assert "INTC" in rep["dropped_tickers"] and "TSLA" in rep["dropped_tickers"]


def test_validate_signals_flags_ungrounded_suggested_zone():
    ev = {"db:entry:INTC": {"id": "db:entry:INTC", "kind": "db", "ticker": "INTC",
                            "payload": {"close": 88.93, "zone_low": 85.0, "zone_high": 90.0}}}
    d = EntrySignalDraft(signals=[EntrySignalView(
        ticker="INTC", signal="near", confidence=0.5, summary="s",
        rationale=[Claim(text="ok", cites=["db:entry:INTC"])],
        invalidation=Claim(text="Invalidation: below 50", cites=["db:entry:INTC"], levels=[50.0]),
        zone_check="lower", suggested_zone=EntryZone(low=40.0, high=45.0))]).model_dump()
    clean, rep = validate_signals(d, ev, ["INTC"])
    s = clean["signals"][0]
    assert s["suggested_zone"]["ungrounded"] is True and rep["ungrounded"] >= 1
    assert s["invalidation"]["text"] == "Below 50"                          # renderer labels the line
    assert s["invalidation"].get("ungrounded") == [50.0]                     # 50 is 15%+ from every cited level


def test_proposed_zone_only_fills_an_empty_row():
    from stockintel.analyst.entry import proposed_zone
    sig = {"suggested_zone": {"low": 55.0, "high": 60.0}, "suggested_invalidation": 50.0}
    assert proposed_zone(sig, {"zone_low": None, "zone_high": None}) == {"zone_low": 55.0, "zone_high": 60.0, "invalidation": 50.0, "clipped": False}
    assert proposed_zone(sig, {"zone_low": 58.0, "zone_high": 63.0}) is None              # user zone wins
    assert proposed_zone({"suggested_zone": {"low": 55.0, "high": 60.0, "ungrounded": True}}, {}) is None
    assert proposed_zone({"suggested_zone": {"low": 61.0, "high": 60.0}}, {}) is None       # inverted
    out = proposed_zone({"suggested_zone": {"low": 55.0, "high": 60.0}, "suggested_invalidation": 57.0}, {})
    assert out["invalidation"] is None                                                     # inside the zone is not an invalidation


def test_proposed_zone_clips_to_the_cost_basis_cap():
    from stockintel.analyst.entry import proposed_zone
    sig = {"suggested_zone": {"low": 55.0, "high": 60.0}, "suggested_invalidation": 50.0}
    out = proposed_zone(sig, {}, cap=58.0)
    assert out["zone_low"] == 55.0 and out["zone_high"] == 58.0 and out["clipped"] is True
    assert proposed_zone(sig, {}, cap=54.0) is None                                          # wholly above the cap
    assert proposed_zone(sig, {}, cap=70.0)["clipped"] is False


def test_add_row_excerpt_and_redaction():
    row = dict(ENTRY_ROWS[0], kind="add", avg_cost=95.0, qty=1.5, zone_low=None, zone_high=None)
    ts = ToolSet(lambda sql, p: [row] if sql is SQL["entry_watch"] else fake_query(sql, p), AS_OF)
    item = ts._ev_entry_watch([row], {})[0]
    assert "ADD to a holding" in item["excerpt"] and "NO ZONE" in item["excerpt"]
    assert "avg_cost" not in item["payload"] and item["payload"]["position_redacted"] is True
    assert item["payload"]["rule"]["vs_cost_pct"] is None                                      # rule read without the basis
    shared = ToolSet(lambda sql, p: [row], AS_OF, share_positions=True)._ev_entry_watch([row], {})[0]
    assert shared["payload"]["avg_cost"] == 95.0 and shared["payload"]["rule"]["vs_cost_pct"] is not None
