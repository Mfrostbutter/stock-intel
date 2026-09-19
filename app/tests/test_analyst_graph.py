"""Headless analyst graph: stub models, recorded rows, no key, no DB, no network.

Proves baseline -> plan -> gather (tool loop) -> draft -> critique -> persist, the citation
and level checks, ticker allow-list, position redaction and the retry-then-invalid path.
Run: python -m pytest tests/test_analyst_graph.py -q
"""

from __future__ import annotations

import sys
from pathlib import Path

from langchain_core.messages import AIMessage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.analyst.graph import build_analyst_graph, pick_research  # noqa: E402
from stockintel.analyst.schemas import (AnalysisDraft, Claim, Critique, EntryView, EntryZone,  # noqa: E402
                                        HoldingView, PlanNotes)
from stockintel.analyst.tools import SQL, ToolSet  # noqa: E402

AS_OF = "2026-09-08"

ROWS = {
    "watchlist": [
        {"ticker": "NVDA", "name": "NVIDIA", "tags": ["ai-compute", "holding"], "target_entry": 200.0, "notes": None, "has_position": True},
        {"ticker": "INTC", "name": "Intel", "tags": ["ai-compute"], "target_entry": None, "notes": None, "has_position": False},
        {"ticker": "SPY", "name": "S&P 500", "tags": ["benchmark"], "target_entry": None, "notes": None, "has_position": False},
    ],
    "benchmarks": [{"ticker": "SPY", "name": "S&P 500", "close": 650.1, "ret_1d": -0.005, "ret_5d": -0.001,
                    "ret_20d": -0.009, "rsi14": 51.5, "sma50": 640.0, "sma200": 600.0}],
    "scores_for_date": [
        {"ticker": "NVDA", "name": "NVIDIA", "tags": ["ai-compute", "holding"], "momentum": 70.0, "value": None,
         "sentiment": 55.0, "catalyst": None, "composite": 64.0, "flags": ["pullback-in-uptrend"], "risks": [],
         "reasons": {"momentum": {"rsi": 41.2}}, "close": 219.32, "rsi14": 41.2, "ret_1d": -0.01, "ret_5d": -0.03,
         "ret_20d": 0.02, "vol_z20": 0.4, "sma20": 228.0, "sma50": 215.0, "sma200": 180.0, "hi52w": 240.0,
         "lo52w": 120.0, "pct_from_hi52w": -0.086, "target_entry": 200.0},
        {"ticker": "INTC", "name": "Intel", "tags": ["ai-compute"], "momentum": 30.0, "value": None, "sentiment": 40.0,
         "catalyst": None, "composite": 34.0, "flags": [], "risks": ["sentiment-collapse"], "reasons": {},
         "close": 88.93, "rsi14": 28.0, "ret_1d": -0.04, "ret_5d": -0.1, "ret_20d": -0.2, "vol_z20": 2.1,
         "sma20": 95.0, "sma50": 100.0, "sma200": 70.0, "hi52w": 110.0, "lo52w": 30.0, "pct_from_hi52w": -0.19,
         "target_entry": None},
    ],
    "holdings": [{"ticker": "NVDA", "name": "NVIDIA", "tags": ["holding"], "qty": 0.455, "avg_cost": 219.32,
                  "opened_at": "2026-06-01", "notes": "core", "close": 219.32, "price_as_of": AS_OF,
                  "cost_basis": 99.79, "market_value": 99.79, "sma20": 228.0, "sma50": 215.0, "sma200": 180.0,
                  "rsi14": 41.2, "ret_1d": -0.01, "ret_5d": -0.03, "ret_20d": 0.02}],
    "run_footer": [{"run_id": "r1", "kind": "daily", "status": "ok", "started_at": None, "finished_at": None,
                    "summary": {"symbols": 2}, "sources": {"alphavantage": {"ok": 0, "error": 0, "quota_skip": 2}}}],
    "score_history": [{"as_of": AS_OF, "composite": 64.0, "momentum": 70.0, "sentiment": 55.0, "flags": [], "risks": []}],
    "price_window": [{"as_of": AS_OF, "open": 220.0, "high": 222.0, "low": 217.0, "close": 219.32, "volume": 1000,
                      "sma20": 228.0, "sma50": 215.0, "sma200": 180.0, "rsi14": 41.2, "atr14": 5.0, "vol_z20": 0.4,
                      "hi52w": 240.0, "lo52w": 120.0, "pct_from_hi52w": -0.086, "ret_1d": -0.01, "ret_5d": -0.03,
                      "ret_20d": 0.02}],
    "news_for_ticker": [{"id": "n1", "headline": "NVDA ships next chip", "source": "finnhub", "url": "https://x/1",
                         "published_at": "2026-09-08T12:00:00+00:00", "sentiment": 0.4, "sentiment_src": "finnhub",
                         "summary": None}],
}


def fake_query(sql: str, params: dict) -> list[dict]:
    for name, tmpl in SQL.items():
        if sql == tmpl:
            return list(ROWS.get(name, []))
    return [{"n": 1}]


class _Structured:
    def __init__(self, value, error=None):
        self.value, self.error = value, error

    def invoke(self, _messages):
        raw = AIMessage(content="", usage_metadata={"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
                        response_metadata={"token_usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.001}})
        if self.error:
            return {"raw": raw, "parsed": None, "parsing_error": self.error}
        return {"raw": raw, "parsed": self.value, "parsing_error": None}


class _Bound:
    def __init__(self, turns):
        self.turns = turns

    def invoke(self, _messages):
        if self.turns:
            calls = self.turns.pop(0)
            return AIMessage(content="", tool_calls=calls,
                             usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55})
        return AIMessage(content="DONE", usage_metadata={"input_tokens": 10, "output_tokens": 1, "total_tokens": 11})


class StubModel:
    def __init__(self, drafts: list, tool_turns: list | None = None):
        self.drafts = drafts
        self.tool_turns = tool_turns if tool_turns is not None else [[
            {"name": "price_window", "args": {"ticker": "NVDA", "days": 30}, "id": "c1"},
            {"name": "news_for_ticker", "args": {"ticker": "NVDA"}, "id": "c2"},
            {"name": "calc", "args": {"expr": "(228 - 219.32) / 219.32"}, "id": "c3"},
            {"name": "db_sql", "args": {"select": "DROP TABLE watchlist"}, "id": "c4"},
        ]]

    plan = PlanNotes(claims_to_source=["NVDA pulled back"], focus="test")

    def with_structured_output(self, schema, include_raw=False):
        if schema is PlanNotes:
            return _Structured(self.plan, error=None if self.plan is not None else ValueError("garbage"))
        if schema is Critique:
            return _Structured(Critique(unsupported=[]))
        return _Structured(self.drafts.pop(0) if len(self.drafts) > 1 else self.drafts[0])

    def bind_tools(self, tools):
        return _Bound(self.tool_turns)


def good_draft() -> AnalysisDraft:
    return AnalysisDraft(
        market_read=[Claim(text="SPY slipped on the day.", cites=[f"db:bench:SPY:{AS_OF}"])],
        holdings=[HoldingView(
            ticker="NVDA", stance="hold", confidence=0.6,
            rationale=[Claim(text="Pullback flag with RSI 41.", cites=[f"db:scores:NVDA:{AS_OF}"]),
                       Claim(text="Close sits below SMA20 at 228.", cites=[f"db:prices:NVDA:{AS_OF}"], levels=[228.0])],
            invalidation=Claim(text="A close below SMA50 near 215.", cites=[f"db:prices:NVDA:{AS_OF}"], levels=[215.0]))],
        entries=[EntryView(
            ticker="NVDA", setup="pullback-in-uptrend", entry_zone=EntryZone(low=210.0, high=219.0),
            invalidation=Claim(text="Below 200.", cites=[f"db:scores:NVDA:{AS_OF}"], levels=[200.0]),
            horizon="weeks", confidence=0.5,
            rationale=[Claim(text="Trend intact above SMA200 at 180.", cites=[f"db:prices:NVDA:{AS_OF}"], levels=[180.0])])],
        avoid=[],
        questions=["Any 8-K this week?"],
        data_gaps=["alphavantage quota_skip 2"],
    )


def bad_draft() -> AnalysisDraft:
    # Unknown citations everywhere plus a ticker off the watchlist and a wild level.
    return AnalysisDraft(
        market_read=[Claim(text="Made up.", cites=["web:99"])],
        holdings=[HoldingView(ticker="NVDA", stance="add", confidence=0.9,
                              rationale=[Claim(text="No source.", cites=["db:nope"])],
                              invalidation=Claim(text="Below 50.", cites=[f"db:scores:NVDA:{AS_OF}"], levels=[50.0]))],
        entries=[EntryView(ticker="TSLA", setup="breakout", entry_zone=EntryZone(low=1, high=2),
                           invalidation=Claim(text="x", cites=[]), horizon="days", confidence=0.1,
                           rationale=[Claim(text="y", cites=[])])],
    )


def _run(model, share_positions=False, hitl=False, prompts=None, critic="same", critic_fallback=None, budgets=None):
    persisted: list[dict] = []
    tools = ToolSet(fake_query, AS_OF, share_positions=share_positions)
    effects = {"load_brief": lambda d: "# brief", "hitl_enabled": lambda: hitl,
               "persist": lambda p: persisted.append(p) or 7}
    prompts = prompts or {k: "{as_of}" for k in ("system", "plan", "gather", "draft", "critique")}
    prompts = {"system": "sys", "plan": "{as_of} {question} {brief} {summary} {max_tickers}",
               "gather": "{as_of} {tickers} {claims} {focus} {question}",
               "draft": "{as_of} {question} {tickers} {index} {focus}", "critique": "{draft} {index}"}
    g = build_analyst_graph(model, tools, effects, prompts, critic=model if critic == "same" else critic,
                            critic_fallback=critic_fallback, budgets=budgets)
    out = g.invoke({"as_of": AS_OF, "mode": "daily", "question": None}, {"recursion_limit": 40})
    return out, persisted, tools


def test_happy_path_persists_ok_with_valid_citations():
    out, persisted, tools = _run(StubModel([good_draft()]))
    assert out["status"] == "ok"
    p = persisted[0]
    assert p["status"] == "ok"
    a = p["analysis"]
    assert a["holdings"][0]["ticker"] == "NVDA"
    assert a["entries"][0]["entry_zone"].get("ungrounded") is None
    assert a["disclaimer"].startswith("Framework, not advice")
    assert p["report"]["stripped"] == 0 and p["report"]["claims"] == 6
    # tool loop ran: template, news, calc all landed in the index; the DROP was rejected as text
    ev = p["evidence"]
    assert f"db:prices:NVDA:{AS_OF}" in ev and "db:news:NVDA:1" in ev and "calc:1" in ev
    assert not any(k.startswith("db:sql:") for k in ev)
    assert tools.calls["db_sql"] == 1
    # cost flowed from usage metadata
    assert p["stats"]["tokens_in"] > 0 and p["stats"]["cost_usd"] > 0
    assert "**NVDA**: hold" in p["markdown"]


def test_plan_research_is_built_by_code():
    out, _, _ = _run(StubModel([good_draft()]))
    assert out["plan"]["research"] == ["NVDA", "INTC"]   # held first, then flagged
    assert out["plan"]["source"] == "code" and out["plan"]["focus"] == "test"


def test_pick_research_order_and_cap():
    ev = {
        "db:holding:AAA": {"ticker": "AAA", "payload": {}},
        "db:scores:BBB:d": {"ticker": "BBB", "payload": {"flags": ["pullback-in-uptrend"], "risks": [], "ret_1d": 0.01}},
        "db:scores:CCC:d": {"ticker": "CCC", "payload": {"flags": [], "risks": ["gap-up-10pct"], "ret_1d": 0.12}},
        "db:scores:DDD:d": {"ticker": "DDD", "payload": {"flags": [], "risks": [], "ret_1d": -0.09}},
        "db:scores:EEE:d": {"ticker": "EEE", "payload": {"flags": [], "risks": [], "ret_1d": 0.002}},
        "db:scores:AAA:d": {"ticker": "AAA", "payload": {"flags": [], "risks": [], "ret_1d": 0.0}},
        "db:scores:ZZZ:d": {"ticker": "ZZZ", "payload": {"flags": ["x"], "risks": [], "ret_1d": 0.5}},
    }
    allowed = {"AAA", "BBB", "CCC", "DDD", "EEE"}
    assert pick_research(ev, allowed, 8) == ["AAA", "BBB", "CCC", "DDD", "EEE"]
    assert pick_research(ev, allowed, 2) == ["AAA", "BBB"]


def test_plan_notes_failure_keeps_the_ticker_list():
    # The model can return garbage for the notes; the research list never depends on it.
    m = StubModel([good_draft()])
    m.plan = None
    out, persisted, _ = _run(m)
    assert out["plan"]["research"] == ["NVDA", "INTC"]
    assert out["plan"]["issue"].startswith("plan notes failed")
    assert f"db:prices:NVDA:{AS_OF}" in persisted[0]["evidence"]
    assert any(i.startswith("plan notes failed") for i in persisted[0]["report"]["issues"])
    assert persisted[0]["status"] == "ok"


def test_positions_redacted_by_default():
    _, persisted, _ = _run(StubModel([good_draft()]))
    h = persisted[0]["evidence"]["db:holding:NVDA"]["payload"]
    assert h.get("position_redacted") is True
    for k in ("qty", "avg_cost", "market_value", "weight", "notes"):
        assert k not in h
    _, persisted, _ = _run(StubModel([good_draft()]), share_positions=True)
    h = persisted[0]["evidence"]["db:holding:NVDA"]["payload"]
    assert h["qty"] == 0.455 and "position_redacted" not in h


def test_bad_draft_retries_once_then_invalid():
    model = StubModel([bad_draft(), bad_draft()])
    out, persisted, _ = _run(model)
    assert out["attempt"] == 2
    assert persisted[0]["status"] == "invalid"
    rep = persisted[0]["report"]
    assert rep["dropped_tickers"] == ["TSLA"]
    assert rep["stripped"] >= 2
    assert any("ungrounded" in i for i in rep["issues"])


def test_bad_then_good_draft_recovers():
    model = StubModel([bad_draft(), good_draft()])
    out, persisted, _ = _run(model)
    assert out["attempt"] == 2 and persisted[0]["status"] == "ok"


def test_gather_turn_budget_caps_the_loop():
    turns = [[{"name": "calc", "args": {"expr": "1+1"}, "id": f"c{i}"}] for i in range(40)]
    # Cites only baseline ids: the capped loop never fetched price_window.
    draft = AnalysisDraft(
        market_read=[Claim(text="SPY slipped.", cites=[f"db:bench:SPY:{AS_OF}"])],
        holdings=[HoldingView(ticker="NVDA", stance="hold", confidence=0.5,
                              rationale=[Claim(text="Pullback flag.", cites=[f"db:scores:NVDA:{AS_OF}"])],
                              invalidation=Claim(text="Below 215.", cites=[f"db:scores:NVDA:{AS_OF}"], levels=[215.0]))])
    model = StubModel([draft], tool_turns=turns)
    tools = ToolSet(fake_query, AS_OF)
    persisted = []
    prompts = {"system": "sys", "plan": "{as_of}{question}{brief}{summary}{max_tickers}",
               "gather": "{as_of}{tickers}{claims}{focus}{question}",
               "draft": "{as_of}{question}{tickers}{index}{focus}", "critique": "{draft}{index}"}
    g = build_analyst_graph(model, tools, {"load_brief": lambda d: "", "hitl_enabled": lambda: False,
                                           "persist": lambda p: persisted.append(p) or 1}, prompts,
                            budgets={"turns": 6})
    g.invoke({"as_of": AS_OF, "mode": "daily"}, {"recursion_limit": 40})
    assert persisted[0]["stats"]["turns"] <= 6 + 2   # gather capped at 6 minus plan, plus draft
    assert "gather stopped at budget" in persisted[0]["report"]["issues"]
    assert persisted[0]["status"] == "ok"


class FailingCritic:
    """with_structured_output that never parses, like an upstream 429."""
    calls = 0

    def with_structured_output(self, schema, include_raw=False):
        FailingCritic.calls += 1
        return _Structured(None, error=RuntimeError("429 upstream"))


def test_gather_prefetch_makes_zero_tool_calls_impossible():
    # Model says DONE at once; history and levels for every research ticker are still in the evidence.
    out, persisted, tools = _run(StubModel([good_draft()], tool_turns=[]))
    ev = persisted[0]["evidence"]
    assert f"db:prices:NVDA:{AS_OF}" in ev and f"db:score_history:NVDA:{AS_OF}" in ev
    assert f"db:prices:INTC:{AS_OF}" in ev
    assert persisted[0]["stats"]["prefetch"] == 4 and persisted[0]["stats"]["tool_calls"] == 0
    assert persisted[0]["status"] == "ok"


def test_critic_falls_back_then_reports():
    FailingCritic.calls = 0
    good = StubModel([good_draft()])
    out, persisted, _ = _run(good, critic=FailingCritic(), critic_fallback=good, budgets={"critic_retry_sleep": 0})
    assert FailingCritic.calls == 2                      # primary tried twice
    assert persisted[0]["status"] == "ok"
    assert any(i.startswith("critic fell back") for i in persisted[0]["report"]["issues"])
    FailingCritic.calls = 0
    out, persisted, _ = _run(good, critic=FailingCritic(), critic_fallback=None, budgets={"critic_retry_sleep": 0})
    assert any(i.startswith("critic unavailable") for i in persisted[0]["report"]["issues"])
    assert persisted[0]["status"] == "ok"                # advisory: code checks still decide
