"""Freshness rules. Headless: no DB, no network."""

from datetime import date, datetime

from stockintel import health

ET = health.ET
# Thanksgiving week 2026: Thu 11-26 closed, Fri 11-27 is a session.
SESS = [date(2026, 11, 23), date(2026, 11, 24), date(2026, 11, 25), date(2026, 11, 27)]


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def fresh(d):
    return {"prices_max": d, "scores_max": d, "report_max": d, "unpriced": [],
            "last_run": {"status": "ok", "started_at": None, "issues": []}, "analysis_status": "ok"}


def test_due_skips_weekend():
    assert health.due_at(date(2026, 11, 27)) == at(2026, 11, 30, 6, 45)


def test_expected_session_waits_for_morning_run():
    assert health.expected_session(at(2026, 11, 25, 6, 0), SESS) == date(2026, 11, 23)
    assert health.expected_session(at(2026, 11, 25, 7, 0), SESS) == date(2026, 11, 24)


def test_holiday_is_not_expected():
    # Friday morning after Thanksgiving: the due session is Wednesday, not the holiday.
    assert health.expected_session(at(2026, 11, 27, 9, 0), SESS) == date(2026, 11, 25)
    out = health.evaluate(fresh(date(2026, 11, 25)), at(2026, 11, 27, 9, 0), SESS)
    assert out["status"] == "ok"


def test_weekend_stays_quiet():
    out = health.evaluate(fresh(date(2026, 11, 25)), at(2026, 11, 29, 12, 0), SESS)
    assert out["status"] == "ok" and out["expected_session"] == date(2026, 11, 25)


def test_missed_run_is_error():
    out = health.evaluate(fresh(date(2026, 11, 24)), at(2026, 11, 27, 9, 0), SESS)
    assert out["status"] == "error"
    assert {c["key"] for c in out["checks"] if not c["ok"]} == {"prices", "scores", "report"}


def test_missing_analysis_is_warn():
    f = fresh(date(2026, 11, 25))
    f["analysis_status"] = None
    out = health.evaluate(f, at(2026, 11, 27, 9, 0), SESS)
    assert out["status"] == "warn"
    assert [c["key"] for c in out["checks"] if not c["ok"]] == ["analysis"]


def test_degraded_run_lists_issues():
    f = fresh(date(2026, 11, 25))
    f["last_run"] = {"status": "degraded", "started_at": None, "issues": ["finnhub failed for 3: A B C"]}
    out = health.evaluate(f, at(2026, 11, 27, 9, 0), SESS)
    assert out["status"] == "warn"
    assert "finnhub failed for 3" in next(c for c in out["checks"] if c["key"] == "run")["message"]


def test_stuck_run_is_error():
    f = fresh(date(2026, 11, 25))
    f["last_run"] = {"status": "running", "started_at": at(2026, 11, 27, 6, 0), "issues": []}
    assert health.evaluate(f, at(2026, 11, 27, 6, 20), SESS)["status"] == "ok"
    assert health.evaluate(f, at(2026, 11, 27, 9, 0), SESS)["status"] == "error"


def test_unpriced_tickers_warn():
    f = fresh(date(2026, 11, 25))
    f["unpriced"] = ["ABC", "XYZ"]
    out = health.evaluate(f, at(2026, 11, 27, 9, 0), SESS)
    assert out["status"] == "warn" and "ABC XYZ" in next(c for c in out["checks"] if c["key"] == "coverage")["message"]


def test_weekday_fallback_has_no_weekends():
    assert all(d.weekday() < 5 for d in health._weekday_sessions(date(2026, 11, 29)))
