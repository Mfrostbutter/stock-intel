"""Daily analyst spend counter math. No DB. Run: python -m pytest tests/test_spend_summary.py -q"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from stockintel.analyst import service  # noqa: E402


def patch(monkeypatch, spent, count, cap):
    monkeypatch.setattr(service.db, "one", lambda *a, **k: {"spent": spent, "n": count, "as_of": "2026-09-17"})
    monkeypatch.setattr(service, "config_values", lambda: {"analyst_daily_cap_usd": cap})


def test_under_cap(monkeypatch):
    patch(monkeypatch, 0.44, 3, 3)
    s = service.spend_summary()
    assert s["spent"] == 0.44 and s["cap"] == 3.0 and s["count"] == 3
    assert s["remaining"] == 2.56 and s["pct_used"] == 0.1467 and s["as_of"] == "2026-09-17"


def test_at_cap_clamps_remaining(monkeypatch):
    patch(monkeypatch, 3.10, 12, 3)
    s = service.spend_summary()
    assert s["remaining"] == 0.0 and s["pct_used"] > 1.0


def test_no_cap_yields_nulls(monkeypatch):
    patch(monkeypatch, 0.5, 2, 0)
    s = service.spend_summary()
    assert s["cap"] is None and s["remaining"] is None and s["pct_used"] is None


def test_zero_spend(monkeypatch):
    patch(monkeypatch, 0, 0, 3)
    s = service.spend_summary()
    assert s["spent"] == 0.0 and s["count"] == 0 and s["remaining"] == 3.0 and s["pct_used"] == 0.0


def test_detail_attaches_today_rows(monkeypatch):
    patch(monkeypatch, 0.30, 2, 3)
    rows = [{"id": 48, "mode": "entry", "model": "deepseek/deepseek-v4-pro-0813", "status": "ok",
             "cost_usd": 0.16, "tokens_in": 100000, "tokens_out": 12000, "turns": 5, "tool_calls": 7,
             "created_at": "2026-09-17T14:00:00", "finished_at": "2026-09-17T14:02:00"},
            {"id": 47, "mode": "daily", "model": "anthropic/claude-sonnet-5", "status": "ok",
             "cost_usd": 0.14, "tokens_in": 90000, "tokens_out": 10000, "turns": 4, "tool_calls": 6,
             "created_at": "2026-09-17T06:05:00", "finished_at": "2026-09-17T06:07:00"}]
    monkeypatch.setattr(service.db, "rows", lambda *a, **k: rows)
    d = service.spend_detail()
    assert d["cap"] == 3.0 and d["spent"] == 0.30
    assert len(d["items"]) == 2 and d["items"][0]["id"] == 48 and d["items"][0]["cost_usd"] == 0.16
