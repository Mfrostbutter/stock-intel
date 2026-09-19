"""All /api routes. Reads are plain SQL over intel; writes are limited to what the UI needs."""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from fastapi import APIRouter, HTTPException, Path, Query
from pydantic import BaseModel, Field, field_validator

from . import alpaca, db, entries, finnhub, health, n8n, providers
from .analyst import service as analyst

log = logging.getLogger("stockintel.api")

router = APIRouter(prefix="/api")
# Same prefix, different auth: n8n's end-of-run call may present the webhook secret instead of the bearer.
hook_router = APIRouter(prefix="/api")

TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _clean_ticker(raw: str) -> str:
    sym = (raw or "").strip().upper()
    if not TICKER_RE.match(sym):
        raise HTTPException(status_code=422, detail=f"'{raw}' is not a plausible ticker")
    return sym


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class WatchlistAdd(BaseModel):
    ticker: str
    tags: list[str] = Field(default_factory=list)
    target_entry: float | None = None
    notes: str | None = None
    backfill: bool = True

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str]) -> list[str]:
        return [t.strip().lower() for t in v if t and t.strip()]


class WatchlistPatch(BaseModel):
    tags: list[str] | None = None
    name: str | None = None
    active: bool | None = None
    target_entry: float | None = None
    notes: str | None = None

    @field_validator("tags")
    @classmethod
    def _clean_tags(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else [t.strip().lower() for t in v if t and t.strip()]


class PositionPut(BaseModel):
    qty: float = Field(gt=0)
    avg_cost: float | None = Field(default=None, ge=0)
    opened_at: date | None = None
    notes: str | None = None


class RunRequest(BaseModel):
    as_of: date | None = None


class ConfigPut(BaseModel):
    values: dict[str, object]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

_REPORT_SUMMARY = r"""
SELECT r.as_of, r.run_id, r.sent_to, r.sent_at,
       (SELECT count(*) FROM scores_daily s
         WHERE s.as_of = r.as_of AND cardinality(s.flags) > 0)  AS setup_count,
       (SELECT count(*) FROM scores_daily s
         WHERE s.as_of = r.as_of AND cardinality(s.risks) > 0)  AS risk_count,
       (SELECT a.status FROM analyses a
         WHERE a.as_of = r.as_of AND a.mode <> 'entry' ORDER BY a.created_at DESC LIMIT 1) AS analysis_status,
       (SELECT a.id FROM analyses a
         WHERE a.as_of = r.as_of AND a.mode <> 'entry' ORDER BY a.created_at DESC LIMIT 1) AS analysis_id,
       regexp_replace(split_part(r.markdown, chr(10), 1), '^#+\s*', '') AS subject
FROM reports r
"""


@router.get("/reports")
def list_reports(limit: int = Query(30, ge=1, le=365)) -> dict:
    return {"reports": db.rows(_REPORT_SUMMARY + " ORDER BY r.as_of DESC LIMIT %s", (limit,))}


@router.get("/reports/{as_of}")
def get_report(as_of: date) -> dict:
    row = db.one(
        "SELECT as_of, run_id, markdown, html, sent_to, sent_at FROM reports WHERE as_of = %s",
        (as_of,),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"No report for {as_of}")

    summary = db.one(_REPORT_SUMMARY + " WHERE r.as_of = %s", (as_of,))
    analysis = db.one(
        "SELECT id, status, model, prompt_version, output, markdown, error, "
        "       tokens_in, tokens_out, cost_usd, created_at, finished_at "
        "FROM analyses WHERE as_of = %s AND mode <> 'entry' ORDER BY created_at DESC LIMIT 1",
        (as_of,),
    )
    health = db.rows(
        "SELECT source, "
        "       count(*) FILTER (WHERE status = 'ok')         AS ok, "
        "       count(*) FILTER (WHERE status = 'error')      AS error, "
        "       count(*) FILTER (WHERE status = 'quota_skip') AS quota_skip "
        "FROM api_usage WHERE run_id = %s GROUP BY source ORDER BY source",
        (row["run_id"],),
    )
    return {"report": row, "summary": summary, "analysis": analysis, "health": health}


@router.get("/reports/{as_of}/scores")
def report_scores(as_of: date) -> dict:
    """Score rows behind one brief. Every number carries its reasons JSON."""
    return {
        "scores": db.rows(
            "SELECT s.ticker, w.name, w.tags, s.momentum, s.value, s.sentiment, s.catalyst, "
            "       s.composite, s.flags, s.risks, s.reasons, "
            "       p.close, i.rsi14, i.ret_1d, i.ret_5d, i.ret_20d, i.vol_z20 "
            "FROM scores_daily s "
            "LEFT JOIN watchlist w        ON w.ticker = s.ticker "
            "LEFT JOIN prices_daily p     ON p.ticker = s.ticker AND p.as_of = s.as_of "
            "LEFT JOIN indicators_daily i ON i.ticker = s.ticker AND i.as_of = s.as_of "
            "WHERE s.as_of = %s ORDER BY s.composite DESC NULLS LAST",
            (as_of,),
        )
    }


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

_WATCHLIST = """
SELECT w.ticker, w.name, w.tags, w.cik, w.active, w.target_entry, w.notes,
       w.added_at, w.retired_at, w.added_by,
       p.as_of AS price_as_of, p.close, p.volume,
       i.rsi14, i.ret_1d, i.ret_5d, i.ret_20d, i.vol_z20,
       i.sma20, i.sma50, i.sma200, i.pct_from_hi52w, i.hi52w, i.lo52w,
       sd.news_score, sd.news_count,
       s.as_of AS score_as_of, s.composite, s.momentum, s.value, s.sentiment, s.catalyst,
       s.flags, s.risks, s.reasons,
       pos.qty, pos.avg_cost
FROM watchlist w
LEFT JOIN LATERAL (SELECT * FROM prices_daily x
                    WHERE x.ticker = w.ticker ORDER BY x.as_of DESC LIMIT 1) p ON true
LEFT JOIN LATERAL (SELECT * FROM indicators_daily x
                    WHERE x.ticker = w.ticker ORDER BY x.as_of DESC LIMIT 1) i ON true
LEFT JOIN LATERAL (SELECT * FROM sentiment_daily x
                    WHERE x.ticker = w.ticker ORDER BY x.as_of DESC LIMIT 1) sd ON true
LEFT JOIN LATERAL (SELECT * FROM scores_daily x
                    WHERE x.ticker = w.ticker ORDER BY x.as_of DESC LIMIT 1) s ON true
LEFT JOIN positions pos ON pos.ticker = w.ticker
ORDER BY w.ticker
"""


@router.get("/watchlist")
def get_watchlist() -> dict:
    rows = db.rows(_WATCHLIST)
    tags = sorted({t for r in rows for t in (r["tags"] or [])})
    return {
        "watchlist": rows,
        "tags": tags,
        "active": sum(1 for r in rows if r["active"]),
        "retired": sum(1 for r in rows if not r["active"]),
    }


def _rederive_holding(sym: str) -> None:
    """Re-apply the derived 'holding' tag after any write that rewrites tags wholesale."""
    db.execute(
        "UPDATE watchlist w SET tags = array_append(w.tags, 'holding') "
        "WHERE w.ticker = %s AND NOT (w.tags @> ARRAY['holding']) "
        "  AND EXISTS (SELECT 1 FROM positions p WHERE p.ticker = w.ticker)",
        (sym,),
    )


def _open_position(sym: str) -> dict | None:
    return db.one("SELECT ticker, qty, avg_cost FROM positions WHERE ticker = %s", (sym,))


def _ensure_watchlist(
    sym: str,
    *,
    tags: list[str] | None = None,
    target_entry: float | None = None,
    notes: str | None = None,
    backfill: bool = True,
) -> dict:
    """Validate against Finnhub, upsert the row, fire the backfill webhook for new tickers."""
    try:
        profile = finnhub.validate(sym)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except finnhub.ValidationUnavailable as e:
        raise HTTPException(status_code=503, detail=f"Cannot validate {sym}: {e}") from e

    row = db.one(
        "INSERT INTO watchlist (ticker, name, tags, cik, active, target_entry, notes, added_by) "
        "VALUES (%s, %s, %s, %s, true, %s, %s, 'ui') "
        "ON CONFLICT (ticker) DO UPDATE SET "
        "  name = COALESCE(EXCLUDED.name, watchlist.name), "
        "  tags = EXCLUDED.tags, "
        "  cik  = COALESCE(EXCLUDED.cik, watchlist.cik), "
        "  active = true, retired_at = NULL, "
        "  target_entry = EXCLUDED.target_entry, "
        "  notes = EXCLUDED.notes "
        "RETURNING ticker, name, tags, cik, active, target_entry, notes, added_at, added_by",
        (sym, profile["name"], tags or [], profile["cik"], target_entry, notes),
    )

    _rederive_holding(sym)

    fired: dict = {"fired": False}
    if backfill:
        # A brand new ticker has no history; an existing one already does.
        stored = db.one("SELECT count(*) AS n FROM prices_daily WHERE ticker = %s", (sym,))["n"]
        if stored:
            fired = {"fired": False, "reason": f"{stored} bars already stored"}
        else:
            try:
                fired = {"fired": True, "response": n8n.fire_backfill([sym])}
            except n8n.WebhookError as e:
                log.warning("backfill webhook failed for %s: %s", sym, e)
                fired = {"fired": False, "error": str(e)}

    return {"ticker": row, "profile": profile, "backfill": fired}


@router.post("/watchlist", status_code=201)
def add_ticker(req: WatchlistAdd) -> dict:
    return _ensure_watchlist(
        _clean_ticker(req.ticker),
        tags=req.tags,
        target_entry=req.target_entry,
        notes=req.notes,
        backfill=req.backfill,
    )


@router.patch("/watchlist/{ticker}")
def patch_ticker(req: WatchlistPatch, ticker: str = Path(...)) -> dict:
    sym = _clean_ticker(ticker)
    sets: list[str] = []
    params: list[object] = []

    if req.active is False and _open_position(sym):
        raise HTTPException(
            status_code=409,
            detail=f"{sym} has an open position; close the position first",
        )

    if req.tags is not None:
        # 'holding' is derived from positions, never set by hand.
        sets.append("tags = %s")
        params.append([t for t in req.tags if t != "holding"])
    if req.name is not None:
        sets.append("name = %s")
        params.append(req.name.strip()[:120] or None)
    if req.target_entry is not None:
        sets.append("target_entry = %s")
        params.append(req.target_entry)
    if req.notes is not None:
        sets.append("notes = %s")
        params.append(req.notes)
    if req.active is not None:
        sets.append("active = %s")
        params.append(req.active)
        sets.append("retired_at = NULL" if req.active else "retired_at = now()")

    if not sets:
        raise HTTPException(status_code=422, detail="Nothing to update")

    params.append(sym)
    row = db.one(
        f"UPDATE watchlist SET {', '.join(sets)} WHERE ticker = %s "
        "RETURNING ticker, name, tags, active, target_entry, notes, retired_at",
        tuple(params),
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"{sym} is not on the watchlist")
    if req.tags is not None:
        _rederive_holding(sym)
        row = db.one(
            "SELECT ticker, name, tags, active, target_entry, notes, retired_at "
            "FROM watchlist WHERE ticker = %s",
            (sym,),
        )
    return {"ticker": row}


# ---------------------------------------------------------------------------
# Positions. The table is the source of truth for "holding"; the tag is a trigger.
# ---------------------------------------------------------------------------

_POSITIONS = """
SELECT pos.ticker, w.name, w.active, pos.qty, pos.avg_cost, pos.opened_at, pos.notes,
       pos.updated_at,
       p.as_of AS price_as_of, p.close AS last_close, prev.close AS prev_close,
       pos.qty * pos.avg_cost                              AS cost_basis,
       pos.qty * p.close                                   AS market_value,
       pos.qty * (p.close - pos.avg_cost)                  AS gain,
       CASE WHEN pos.avg_cost > 0
            THEN (p.close - pos.avg_cost) / pos.avg_cost END AS gain_pct,
       pos.qty * (p.close - prev.close)                    AS day_change,
       i.ret_1d
FROM positions pos
JOIN watchlist w ON w.ticker = pos.ticker
LEFT JOIN LATERAL (SELECT x.as_of, x.close FROM prices_daily x
                    WHERE x.ticker = pos.ticker ORDER BY x.as_of DESC LIMIT 1) p ON true
LEFT JOIN LATERAL (SELECT x.close FROM prices_daily x
                    WHERE x.ticker = pos.ticker AND x.as_of < p.as_of
                    ORDER BY x.as_of DESC LIMIT 1) prev ON true
LEFT JOIN LATERAL (SELECT x.ret_1d FROM indicators_daily x
                    WHERE x.ticker = pos.ticker ORDER BY x.as_of DESC LIMIT 1) i ON true
ORDER BY pos.ticker
"""


def _positions_payload() -> dict:
    rows = db.rows(_POSITIONS)
    equity = sum(r["market_value"] or 0 for r in rows)
    for r in rows:
        r["weight"] = (r["market_value"] / equity) if (equity and r["market_value"]) else None

    cost = sum(r["cost_basis"] or 0 for r in rows)
    gain = sum(r["gain"] or 0 for r in rows)
    total = {
        "count": len(rows),
        "cost_basis": cost or None,
        "market_value": equity or None,
        "gain": gain if rows else None,
        "gain_pct": (gain / cost) if cost else None,
        "day_change": sum(r["day_change"] or 0 for r in rows) if rows else None,
    }
    return {"positions": rows, "total": total}


@router.get("/positions")
def list_positions() -> dict:
    """Open positions valued against the latest stored close, so the card is live
    even when the brief on screen is a day old."""
    return _positions_payload()


@router.put("/positions/{ticker}")
def put_position(req: PositionPut, ticker: str = Path(...)) -> dict:
    sym = _clean_ticker(ticker)

    added = None
    if not db.one("SELECT 1 AS ok FROM watchlist WHERE ticker = %s", (sym,)):
        added = _ensure_watchlist(sym)

    with db.tx() as cur:
        cur.execute(
            "INSERT INTO positions (ticker, qty, avg_cost, opened_at, notes, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, now()) "
            "ON CONFLICT (ticker) DO UPDATE SET "
            "  qty = EXCLUDED.qty, avg_cost = EXCLUDED.avg_cost, "
            "  opened_at = COALESCE(EXCLUDED.opened_at, positions.opened_at), "
            "  notes = EXCLUDED.notes, updated_at = now() "
            "RETURNING ticker, qty, avg_cost, opened_at, notes, updated_at",
            (sym, req.qty, req.avg_cost, req.opened_at, req.notes),
        )
        row = cur.fetchone()

    return {"position": row, "added_to_watchlist": added, **_positions_payload()}


@router.delete("/positions/{ticker}")
def close_position(ticker: str = Path(...)) -> dict:
    """Close: the trigger writes a qty = 0 history row and drops the 'holding' tag."""
    sym = _clean_ticker(ticker)
    with db.tx() as cur:
        cur.execute("DELETE FROM positions WHERE ticker = %s RETURNING ticker", (sym,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"{sym} has no open position")
    return {"closed": sym, **_positions_payload()}


@router.get("/positions/{ticker}/history")
def position_history(ticker: str = Path(...)) -> dict:
    sym = _clean_ticker(ticker)
    return {
        "ticker": sym,
        "history": db.rows(
            "SELECT id, qty, avg_cost, opened_at, notes, changed_at, changed_by "
            "FROM position_history WHERE ticker = %s ORDER BY changed_at DESC, id DESC",
            (sym,),
        ),
    }


# ---------------------------------------------------------------------------
# Entry watch. Tickers being watched for an entry; signals come from the rule
# layer at once and from the analyst's entry pass. Framework, not advice.
# ---------------------------------------------------------------------------

ENTRY_HORIZONS = ("days", "weeks", "months")
ENTRY_STATUSES = ("watching", "triggered", "entered", "dropped")


class EntryAdd(BaseModel):
    ticker: str
    thesis: str | None = Field(None, max_length=2000)
    zone_low: float | None = Field(None, gt=0)
    zone_high: float | None = Field(None, gt=0)
    invalidation: float | None = Field(None, gt=0)
    horizon: str = Field("weeks", pattern="^(days|weeks|months)$")
    notes: str | None = Field(None, max_length=2000)
    backfill: bool = True
    signal_now: bool = True     # run the analyst on this ticker at once; it proposes a zone when none was given


class EntryPatch(BaseModel):
    thesis: str | None = Field(None, max_length=2000)
    zone_low: float | None = Field(None, gt=0)
    zone_high: float | None = Field(None, gt=0)
    invalidation: float | None = Field(None, gt=0)
    horizon: str | None = Field(None, pattern="^(days|weeks|months)$")
    status: str | None = Field(None, pattern="^(watching|triggered|entered|dropped)$")
    notes: str | None = Field(None, max_length=2000)


class EntrySignalRequest(BaseModel):
    tickers: list[str] | None = None
    model: str | None = Field(None, max_length=120)


def _check_zone(low: float | None, high: float | None) -> None:
    if low is not None and high is not None and low > high:
        raise HTTPException(status_code=422, detail="zone_low must not exceed zone_high")


_ENTRIES = """
SELECT e.ticker, w.name, w.active, w.tags, e.thesis, e.zone_low, e.zone_high, e.invalidation, e.horizon,
       e.status, e.zone_source, e.kind, e.notes, e.added_at, e.updated_at,
       p.as_of AS price_as_of, p.close,
       i.sma20, i.sma50, i.sma200, i.rsi14, i.vol_z20, i.hi52w, i.lo52w, i.ret_1d, i.ret_5d, i.ret_20d,
       sd.news_score, sd.news_count,
       s.as_of AS score_as_of, s.composite, s.flags, s.risks,
       pos.qty, pos.avg_cost,
       sig.id AS signal_id, sig.signal, sig.confidence, sig.summary, sig.rationale,
       sig.invalidation AS signal_invalidation, sig.zone_check, sig.suggested_zone, sig.suggested_invalidation, sig.watch_for,
       sig.rule AS signal_rule, sig.ref_price, sig.model AS signal_model, sig.analysis_id,
       sig.as_of AS signal_as_of, sig.created_at AS signal_at
FROM entry_watch e
JOIN watchlist w ON w.ticker = e.ticker
LEFT JOIN LATERAL (SELECT * FROM prices_daily x
                    WHERE x.ticker = e.ticker ORDER BY x.as_of DESC LIMIT 1) p ON true
LEFT JOIN LATERAL (SELECT * FROM indicators_daily x
                    WHERE x.ticker = e.ticker ORDER BY x.as_of DESC LIMIT 1) i ON true
LEFT JOIN LATERAL (SELECT * FROM sentiment_daily x
                    WHERE x.ticker = e.ticker ORDER BY x.as_of DESC LIMIT 1) sd ON true
LEFT JOIN LATERAL (SELECT * FROM scores_daily x
                    WHERE x.ticker = e.ticker ORDER BY x.as_of DESC LIMIT 1) s ON true
LEFT JOIN LATERAL (SELECT * FROM entry_signals x
                    WHERE x.ticker = e.ticker ORDER BY x.created_at DESC, x.id DESC LIMIT 1) sig ON true
LEFT JOIN positions pos ON pos.ticker = e.ticker
"""


def _near_pct() -> float:
    row = db.one("SELECT value FROM config WHERE key = 'entry_near_pct'")
    try:
        return float(row["value"]) if row else entries.NEAR_PCT
    except (TypeError, ValueError):
        return entries.NEAR_PCT


def _dca_discount() -> float:
    row = db.one("SELECT value FROM config WHERE key = 'entry_dca_min_discount'")
    try:
        return float(row["value"]) if row else 0.02
    except (TypeError, ValueError):
        return 0.02


def _entries_payload(include_dropped: bool = False) -> dict:
    analyst.ensure_holding_rows(actor="ui")
    rows = db.rows(_ENTRIES + ("" if include_dropped else " WHERE e.status <> 'dropped'") + " ORDER BY e.ticker")
    near = _near_pct()
    discount = _dca_discount()
    for r in rows:
        r["rule"] = entries.rule_signal(r, near_pct=near)
        r["zone_cap"] = entries.add_zone_cap(r["avg_cost"], discount) if r["kind"] == "add" else None
    rows.sort(key=lambda r: (entries.signal_rank(r["signal"] or r["rule"]["signal"]),
                             entries.signal_rank(r["rule"]["signal"]), r["ticker"]))
    running = db.rows("SELECT id, created_at FROM analyses WHERE mode = 'entry' AND status IN ('pending', 'running') "
                      "ORDER BY id")
    last_pass = db.one("SELECT id, status, model, as_of, created_at, finished_at, cost_usd, turns, tool_calls, stripped "
                       "FROM analyses WHERE mode = 'entry' AND status NOT IN ('pending', 'running') "
                       "ORDER BY created_at DESC LIMIT 1")
    counts = {s: sum(1 for r in rows if r["status"] == s) for s in ENTRY_STATUSES}
    return {"entries": rows, "counts": counts, "running": running, "last_pass": last_pass, "near_pct": near}


@router.get("/entries")
def get_entries(include_dropped: bool = False) -> dict:
    return _entries_payload(include_dropped)


@router.post("/entries", status_code=201)
def add_entry(req: EntryAdd) -> dict:
    sym = _clean_ticker(req.ticker)
    _check_zone(req.zone_low, req.zone_high)
    added = None
    if not db.one("SELECT 1 AS ok FROM watchlist WHERE ticker = %s", (sym,)):
        added = _ensure_watchlist(sym, tags=["entry-watch"], backfill=req.backfill)
    elif not db.one("SELECT 1 AS ok FROM watchlist WHERE ticker = %s AND active", (sym,)):
        db.execute("UPDATE watchlist SET active = true, retired_at = NULL WHERE ticker = %s", (sym,))
    # A ticker already held is an add row: its zone tops out below the cost basis.
    pos = _open_position(sym)
    kind = "add" if pos else "new"
    if kind == "add":
        cap = entries.add_zone_cap(pos["avg_cost"], _dca_discount())
        if cap is not None and req.zone_low is not None and req.zone_low >= cap:
            raise HTTPException(status_code=422, detail=f"{sym} is held; an add zone must sit below {cap:.2f} to lower the average cost")
        if cap is not None and req.zone_high is not None and req.zone_high > cap:
            req.zone_high = cap
    with db.tx() as cur:
        cur.execute(
            "INSERT INTO entry_watch (ticker, thesis, zone_low, zone_high, invalidation, horizon, notes, added_by, zone_source, kind) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, 'ui', 'user', %s) "
            "ON CONFLICT (ticker) DO UPDATE SET thesis = EXCLUDED.thesis, zone_low = EXCLUDED.zone_low, "
            "  zone_high = EXCLUDED.zone_high, invalidation = EXCLUDED.invalidation, horizon = EXCLUDED.horizon, "
            "  notes = EXCLUDED.notes, status = 'watching', zone_source = 'user', kind = EXCLUDED.kind "
            "RETURNING ticker, status, kind, added_at",
            (sym, req.thesis, req.zone_low, req.zone_high, req.invalidation, req.horizon, req.notes, kind))
        row = cur.fetchone()
    signal: dict = {"fired": False}
    if req.signal_now:
        # A fresh ticker may still be backfilling; the pass reads whatever bars exist and says so.
        try:
            signal = {"fired": True, **analyst.start_entry_signals(tickers=[sym])}
        except analyst.AnalystError as e:
            signal = {"fired": False, "reason": e.detail}
    return {"entry": row, "added_to_watchlist": added, "signal": signal, **_entries_payload()}


@router.patch("/entries/{ticker}")
def patch_entry(req: EntryPatch, ticker: str = Path(...)) -> dict:
    sym = _clean_ticker(ticker)
    changes = req.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="Nothing to update")
    cur_row = db.one("SELECT zone_low, zone_high, kind FROM entry_watch WHERE ticker = %s", (sym,))
    if not cur_row:
        raise HTTPException(status_code=404, detail=f"{sym} is not on the entry watch list")
    _check_zone(changes.get("zone_low", cur_row["zone_low"]), changes.get("zone_high", cur_row["zone_high"]))
    if "zone_low" in changes or "zone_high" in changes or "invalidation" in changes:
        changes["zone_source"] = "user"     # a hand edit owns the zone from here on
    if cur_row["kind"] == "add" and "zone_low" in changes and changes["zone_low"] is not None:
        pos = _open_position(sym)
        cap = entries.add_zone_cap(pos["avg_cost"], _dca_discount()) if pos else None
        if cap is not None and changes["zone_low"] >= cap:
            raise HTTPException(status_code=422, detail=f"{sym} is held; an add zone must sit below {cap:.2f} to lower the average cost")
    if cur_row["kind"] == "add" and changes.get("status") == "entered":
        # An add fill lowers the average; the row goes straight back to watching for the next dip,
        # zone cleared so the next pass proposes it against the new cost basis.
        changes.update(status="watching", zone_low=None, zone_high=None, invalidation=None, zone_source="user")
    sets = [f"{k} = %s" for k in changes]
    params: list[object] = list(changes.values()) + [sym]
    with db.tx() as cur:
        cur.execute(f"UPDATE entry_watch SET {', '.join(sets)} WHERE ticker = %s RETURNING ticker, status", tuple(params))
        row = cur.fetchone()
    return {"entry": row, **_entries_payload()}


@router.delete("/entries/{ticker}")
def drop_entry(ticker: str = Path(...)) -> dict:
    """Archive, never delete: the row moves to dropped and its history and signals stay."""
    sym = _clean_ticker(ticker)
    with db.tx() as cur:
        cur.execute("UPDATE entry_watch SET status = 'dropped' WHERE ticker = %s RETURNING ticker", (sym,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail=f"{sym} is not on the entry watch list")
    return {"dropped": sym, **_entries_payload()}


@router.post("/entries/{ticker}/accept-zone")
def accept_zone(ticker: str = Path(...)) -> dict:
    """Take the latest signal's suggested zone as the row's zone, marked analyst-owned."""
    sym = _clean_ticker(ticker)
    sig = db.one("SELECT suggested_zone, suggested_invalidation FROM entry_signals WHERE ticker = %s "
                 "ORDER BY created_at DESC, id DESC LIMIT 1", (sym,))
    z = (sig or {}).get("suggested_zone") or {}
    if z.get("low") is None or z.get("high") is None or z.get("ungrounded"):
        raise HTTPException(status_code=409, detail=f"{sym} has no grounded zone suggestion to accept")
    low, high = float(z["low"]), float(z["high"])
    cap = analyst.add_zone_caps([sym]).get(sym)
    if cap is not None:
        if low >= cap:
            raise HTTPException(status_code=409, detail=f"{sym} suggestion sits above the cost basis cap {cap:.2f}; adding there would not lower the average")
        high = min(high, cap)
    inv = sig.get("suggested_invalidation")
    with db.tx("analyst") as cur:
        cur.execute(
            "UPDATE entry_watch SET zone_low = %s, zone_high = %s, invalidation = COALESCE(%s, invalidation), "
            "  zone_source = 'analyst' WHERE ticker = %s RETURNING ticker, zone_low, zone_high, invalidation",
            (low, high, inv if inv is not None and float(inv) < low else None, sym))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"{sym} is not on the entry watch list")
    return {"entry": row, **_entries_payload()}


@router.get("/entries/{ticker}/signals")
def entry_signals(ticker: str = Path(...), limit: int = Query(60, ge=1, le=500)) -> dict:
    sym = _clean_ticker(ticker)
    return {
        "ticker": sym,
        "signals": db.rows(
            "SELECT id, as_of, signal, confidence, summary, rationale, invalidation, zone_check, suggested_zone, "
            "       watch_for, rule, ref_price, model, analysis_id, cost_usd, created_at "
            "FROM entry_signals WHERE ticker = %s ORDER BY created_at DESC, id DESC LIMIT %s", (sym, limit)),
        "history": db.rows(
            "SELECT id, status, zone_low, zone_high, invalidation, horizon, thesis, kind, changed_at, changed_by "
            "FROM entry_watch_history WHERE ticker = %s ORDER BY changed_at DESC, id DESC LIMIT 100", (sym,)),
    }


@hook_router.post("/entries/signal", status_code=202)
def signal_entries(req: EntrySignalRequest | None = None) -> dict:
    """Run the analyst's entry pass now. Called from the UI and, when auto is off, from n8n."""
    req = req or EntrySignalRequest()
    tickers = [_clean_ticker(t) for t in (req.tickers or [])] or None
    try:
        return analyst.start_entry_signals(tickers=tickers, model_override=req.model)
    except analyst.AnalystError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


# ---------------------------------------------------------------------------
# Ticker detail. Endpoints land now so the phase B screen is the only lift left.
# ---------------------------------------------------------------------------

@router.get("/tickers/{ticker}/prices")
def ticker_prices(ticker: str = Path(...), days: int = Query(400, ge=5, le=2000)) -> dict:
    sym = _clean_ticker(ticker)
    return {
        "ticker": sym,
        "bars": db.rows(
            "SELECT p.as_of, p.open, p.high, p.low, p.close, p.volume, "
            "       i.sma20, i.sma50, i.sma200, i.rsi14, i.atr14, i.vol_z20 "
            "FROM prices_daily p "
            "LEFT JOIN indicators_daily i ON i.ticker = p.ticker AND i.as_of = p.as_of "
            "WHERE p.ticker = %s AND p.as_of > current_date - %s::int "
            "ORDER BY p.as_of",
            (sym, days),
        ),
    }


@router.get("/tickers/{ticker}/news")
def ticker_news(ticker: str = Path(...), days: int = Query(14, ge=1, le=180)) -> dict:
    sym = _clean_ticker(ticker)
    return {
        "ticker": sym,
        "articles": db.rows(
            "SELECT id, published_at, source, headline, url, summary, sentiment, sentiment_src "
            "FROM news_articles "
            "WHERE ticker = %s AND published_at > now() - make_interval(days => %s) "
            "ORDER BY published_at DESC LIMIT 200",
            (sym, days),
        ),
    }


@router.get("/tickers/{ticker}/scores")
def ticker_scores(ticker: str = Path(...), days: int = Query(90, ge=5, le=1000)) -> dict:
    sym = _clean_ticker(ticker)
    return {
        "ticker": sym,
        "scores": db.rows(
            "SELECT as_of, momentum, value, sentiment, catalyst, composite, flags, risks, reasons "
            "FROM scores_daily WHERE ticker = %s AND as_of > current_date - %s::int "
            "ORDER BY as_of",
            (sym, days),
        ),
    }


# ---------------------------------------------------------------------------
# Live quotes. prices_daily only moves once a day; this is what makes the UI current.
# ---------------------------------------------------------------------------

@router.get("/quotes")
def get_quotes(tickers: str = Query("", description="CSV. Empty means every active ticker")) -> dict:
    syms = [t for t in (tickers or "").upper().split(",") if t.strip()]
    if not syms:
        syms = [r["ticker"] for r in db.rows("SELECT ticker FROM watchlist WHERE active ORDER BY ticker")]

    open_now = alpaca.market_open()
    try:
        payload = alpaca.quotes(syms)
    except alpaca.QuotesUnavailable as e:
        # A dead quote feed must not blank the screen; the caller keeps its stored closes.
        return {"quotes": {}, "fetched_at": None, "cached": False,
                "market_open": open_now, "feed": None, "error": str(e)}

    return {**payload, "market_open": open_now, "feed": alpaca.QUOTE_FEED}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

@router.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200)) -> dict:
    return {
        "runs": db.rows(
            "SELECT r.run_id, r.kind, r.as_of, r.started_at, r.finished_at, r.status, r.summary, "
            "  COALESCE(( "
            "    SELECT jsonb_agg(t ORDER BY t.source) FROM ( "
            "      SELECT u.source, "
            "             count(*) FILTER (WHERE u.status = 'ok')         AS ok, "
            "             count(*) FILTER (WHERE u.status = 'error')      AS error, "
            "             count(*) FILTER (WHERE u.status = 'quota_skip') AS quota_skip "
            "      FROM api_usage u WHERE u.run_id = r.run_id GROUP BY u.source) t "
            "  ), '[]'::jsonb) AS sources "
            "FROM runs r ORDER BY r.started_at DESC LIMIT %s",
            (limit,),
        )
    }


@router.get("/health/data")
def data_health() -> dict:
    """Did the last due session land end to end: prices, scores, brief, analysis."""
    return health.report()


@router.post("/runs/daily")
def run_daily(req: RunRequest) -> dict:
    try:
        payload = n8n.fire_daily(req.as_of.isoformat() if req.as_of else None)
    except n8n.WebhookError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    return {"fired": True, "response": payload}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@router.get("/config")
def get_config() -> dict:
    rows = db.rows("SELECT key, value, updated_at FROM config ORDER BY key")
    return {
        "config": {r["key"]: r["value"] for r in rows},
        "updated_at": {r["key"]: r["updated_at"] for r in rows},
    }


@router.put("/config")
def put_config(req: ConfigPut) -> dict:
    if not req.values:
        raise HTTPException(status_code=422, detail="No config values supplied")
    for key, value in req.values.items():
        db.execute(
            "INSERT INTO config (key, value, updated_at) VALUES (%s, %s::jsonb, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()",
            (key, json.dumps(value)),
        )
    return get_config()


# ---------------------------------------------------------------------------
# Analyst
# ---------------------------------------------------------------------------

class AnalyzeRequest(BaseModel):
    mode: str = Field("daily", pattern="^(daily|question|deep)$")
    question: str | None = Field(None, max_length=600)
    experiment: str | None = Field(None, max_length=60)
    model: str | None = Field(None, max_length=120, description="OpenRouter id override for bake-off runs")


class FeedbackRequest(BaseModel):
    rating: str = Field(..., pattern="^(up|down)$")
    note: str | None = Field(None, max_length=2000)


@hook_router.post("/reports/{as_of}/analyze", status_code=202)
def analyze_report(as_of: date, req: AnalyzeRequest | None = None) -> dict:
    """Start the analyst in the background. Called by Daily's last node and by the UI's Re-analyze."""
    req = req or AnalyzeRequest()
    try:
        return analyst.start_analysis(as_of, mode=req.mode, question=req.question,
                                      experiment=req.experiment, model_override=req.model)
    except analyst.AnalystError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from e


@router.get("/analyses")
def list_analyses(as_of: date | None = None, mode: str | None = Query(None, pattern="^(daily|question|deep|entry)$"),
                  limit: int = Query(30, ge=1, le=200)) -> dict:
    conds, params = [], []
    if as_of:
        conds.append("as_of = %s")
        params.append(as_of)
    if mode:
        conds.append("mode = %s")
        params.append(mode)
    where = (" WHERE " + " AND ".join(conds)) if conds else ""
    return {"analyses": db.rows(
        "SELECT id, as_of, model, status, mode, experiment, tokens_in, tokens_out, cost_usd, turns, tool_calls, "
        "       stripped, created_at, finished_at FROM analyses" + where +
        " ORDER BY created_at DESC LIMIT %s", (*params, limit))}


@router.get("/analyst/spend")
def analyst_spend() -> dict:
    """Today's analyst spend vs the daily cap, for the sidebar counter."""
    return analyst.spend_summary()


@router.get("/analyst/spend/detail")
def analyst_spend_detail() -> dict:
    """spend plus today's per-pass rows, for the click-through detail view."""
    return analyst.spend_detail()


@router.get("/analyses/{analysis_id}")
def get_analysis(analysis_id: int = Path(..., ge=1)) -> dict:
    row = analyst.get_analysis(analysis_id)
    if not row:
        raise HTTPException(status_code=404, detail="No such analysis")
    return {"analysis": row}


@router.post("/analyses/{analysis_id}/feedback", status_code=201)
def analysis_feedback(req: FeedbackRequest, analysis_id: int = Path(..., ge=1)) -> dict:
    if not db.one("SELECT 1 FROM analyses WHERE id = %s", (analysis_id,)):
        raise HTTPException(status_code=404, detail="No such analysis")
    with db.tx("ui") as cur:
        cur.execute("INSERT INTO analysis_feedback (analysis_id, rating, note) VALUES (%s, %s, %s) RETURNING id",
                    (analysis_id, req.rating, (req.note or "").strip() or None))
        fid = cur.fetchone()["id"]
    return {"id": fid, "analysis_id": analysis_id, "rating": req.rating}


# ---------------------------------------------------------------------------
# Model providers (Settings > Models)
# ---------------------------------------------------------------------------

class ProvidersPut(BaseModel):
    providers: list[dict]


@router.get("/providers")
def get_providers() -> dict:
    return {"providers": providers.with_status(providers.registry()), "kinds": providers.KINDS,
            "default": providers.DEFAULT_PROVIDER}


@router.put("/providers")
def put_providers(req: ProvidersPut) -> dict:
    try:
        return {"providers": providers.save(req.providers)}
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


@router.post("/providers/{provider_id}/test")
def test_provider(provider_id: str = Path(..., pattern="^[a-z][a-z0-9_-]{1,31}$")) -> dict:
    try:
        return providers.test(provider_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


@router.get("/models")
def list_models(provider: str = Query(providers.DEFAULT_PROVIDER, pattern="^[a-z][a-z0-9_-]{1,31}$"),
                refresh: bool = False) -> dict:
    """Live catalog for one provider, cached five minutes. Falls back to the configured ids."""
    try:
        return providers.list_models(provider, force=refresh)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
