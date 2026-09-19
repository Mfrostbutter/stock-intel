"""Daily pipeline freshness. Answers: did the last due session land end to end?

evaluate() is pure so it tests without a DB; gather() reads intel and the Alpaca calendar.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta
from zoneinfo import ZoneInfo

import requests

from . import db
from .config import ALPACA_KEY_ID, ALPACA_SECRET_KEY, HTTP_TIMEOUT

log = logging.getLogger("stockintel.health")

ET = ZoneInfo("America/New_York")
CALENDAR_URL = "https://paper-api.alpaca.markets/v2/calendar"
CALENDAR_TTL = 6 * 3600
DUE_AT = dtime(6, 45)  # Daily fires 06:00 ET on weekdays
STUCK_MINUTES = 30

_lock = threading.Lock()
_cal: dict[str, object] = {"at": 0.0, "sessions": []}


def _weekday_sessions(today: date, days: int = 21) -> list[date]:
    out = [today - timedelta(days=i) for i in range(days, -1, -1)]
    return [d for d in out if d.weekday() < 5]


def sessions(today: date) -> tuple[list[date], bool]:
    """Recent trading sessions and whether they came from the real calendar."""
    with _lock:
        if _cal["sessions"] and time.time() - float(_cal["at"]) < CALENDAR_TTL:
            return list(_cal["sessions"]), True
    if ALPACA_KEY_ID and ALPACA_SECRET_KEY:
        try:
            resp = requests.get(
                CALENDAR_URL,
                params={"start": str(today - timedelta(days=21)), "end": str(today)},
                headers={"APCA-API-KEY-ID": ALPACA_KEY_ID, "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY},
                timeout=HTTP_TIMEOUT,
            )
            if resp.status_code == 200:
                got = sorted(date.fromisoformat(c["date"]) for c in resp.json())
                if got:
                    with _lock:
                        _cal.update(at=time.time(), sessions=got)
                    return got, True
        except (requests.RequestException, ValueError, KeyError) as e:
            log.warning("calendar unavailable: %s", e)
    return _weekday_sessions(today), False


def due_at(session: date) -> datetime:
    """When the run covering this session should have finished: next weekday, 06:45 ET."""
    d = session + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime.combine(d, DUE_AT, tzinfo=ET)


def expected_session(now: datetime, sess: list[date]) -> date | None:
    due = [s for s in sess if due_at(s) <= now]
    return max(due) if due else None


def _check(key: str, ok: bool, message: str, severity: str = "error") -> dict:
    return {"key": key, "ok": ok, "severity": None if ok else severity, "message": message}


def evaluate(facts: dict, now: datetime, sess: list[date], calendar_ok: bool = True) -> dict:
    exp = expected_session(now, sess)
    checks: list[dict] = []
    if exp is None:
        return {"status": "ok", "expected_session": None, "calendar_ok": calendar_ok, "checks": [], "checked_at": now.isoformat()}

    for key, label in (("prices", "Prices"), ("scores", "Scores"), ("report", "Brief")):
        have = facts.get(f"{key}_max")
        ok = have is not None and have >= exp
        checks.append(_check(key, ok, f"{label} current through {have}" if ok else f"{label} stop at {have or 'nothing'}, expected {exp}"))

    unpriced = facts.get("unpriced") or []
    if facts.get("prices_max") is not None and facts["prices_max"] >= exp and unpriced:
        checks.append(_check("coverage", False, f"No {facts['prices_max']} price for {len(unpriced)}: {' '.join(unpriced[:8])}", "warn"))

    run = facts.get("last_run") or {}
    status = run.get("status")
    if status == "running":
        age = (now - run["started_at"]).total_seconds() / 60 if run.get("started_at") else 0
        if age > STUCK_MINUTES:
            checks.append(_check("run", False, f"Daily run has been running for {int(age)} min"))
    elif status == "failed":
        checks.append(_check("run", False, "Last daily run failed: " + "; ".join(run.get("issues") or ["no detail"])))
    elif status == "degraded":
        checks.append(_check("run", False, "Last daily run degraded: " + "; ".join(run.get("issues") or ["no detail"]), "warn"))
    elif status:
        checks.append(_check("run", True, f"Last daily run {status}"))

    a = facts.get("analysis_status")
    if facts.get("report_max") is not None and facts["report_max"] >= exp:
        ok = a in ("ok", "pending", "running")
        checks.append(_check("analysis", ok, f"Daily analysis {a}" if ok else f"No usable daily analysis for the {facts['report_max']} brief ({a or 'none'})", "warn"))

    bad = [c for c in checks if not c["ok"]]
    overall = "error" if any(c["severity"] == "error" for c in bad) else ("warn" if bad else "ok")
    return {"status": overall, "expected_session": exp, "calendar_ok": calendar_ok, "checks": checks, "checked_at": now.isoformat()}


def gather() -> dict:
    row = db.one(
        "SELECT (SELECT max(as_of) FROM prices_daily WHERE ticker = 'SPY') AS prices_max, "
        "       (SELECT max(as_of) FROM scores_daily) AS scores_max, "
        "       (SELECT max(as_of) FROM reports) AS report_max"
    ) or {}
    facts: dict = dict(row)
    if facts.get("prices_max"):
        facts["unpriced"] = [
            r["ticker"] for r in db.rows(
                "SELECT w.ticker FROM watchlist w WHERE w.active AND NOT EXISTS "
                "(SELECT 1 FROM prices_daily p WHERE p.ticker = w.ticker AND p.as_of = %s) ORDER BY 1",
                (facts["prices_max"],),
            )
        ]
    run = db.one("SELECT status, started_at, summary FROM runs WHERE kind = 'daily' ORDER BY started_at DESC LIMIT 1")
    if run:
        summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
        facts["last_run"] = {"status": run["status"], "started_at": run["started_at"], "issues": summary.get("issues") or []}
    if facts.get("report_max"):
        a = db.one(
            "SELECT status FROM analyses WHERE mode = 'daily' AND as_of = %s "
            "ORDER BY (status = 'ok') DESC, id DESC LIMIT 1",
            (facts["report_max"],),
        )
        facts["analysis_status"] = a["status"] if a else None
    return facts


def report() -> dict:
    now = datetime.now(ET)
    sess, calendar_ok = sessions(now.date())
    out = evaluate(gather(), now, sess, calendar_ok)
    if out["status"] != "ok":
        log.warning("data health %s: %s", out["status"], "; ".join(c["message"] for c in out["checks"] if not c["ok"]) or "no failing check")
    return out
