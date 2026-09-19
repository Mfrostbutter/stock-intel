"""Run the analyst inside the app: read-only query fn, config, models, persist, spend cap.

One background worker; one analysis per as_of in flight. The graph itself is pure (graph.py);
everything that touches Postgres, env or OpenRouter lives here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from .. import db, providers
from ..config import APP_ROOT, PG_ANALYST_PASSWORD, PG_ANALYST_USER, PG_DB, PG_HOST, PG_PORT
from ..entries import add_zone_cap
from . import openrouter
from .entry import ENTRY_PROFILE, cite_ids_signals, proposed_zone
from .evidence import cite_ids_draft
from .graph import build_analyst_graph
from .tools import ToolSet, jsonable

log = logging.getLogger("stockintel.analyst")

PROMPT_FILES = {"system": "analyst_system_v1.md", "plan": "analyst_plan_v1.md", "gather": "analyst_gather_v1.md",
                "draft": "analyst_draft_v1.md", "critique": "analyst_critique_v1.md",
                "entry_gather": "entry_gather_v1.md", "entry_draft": "entry_draft_v1.md"}
DAILY_PROMPT_KEYS = ("system", "plan", "gather", "draft", "critique")
ENTRY_PROMPT_KEYS = ("system", "entry_gather", "entry_draft", "critique")
ENTRY_STATUSES_LIVE = ("watching", "triggered")

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="analyst")
_inflight: dict[str, int] = {}
_lock = threading.Lock()


def _j(obj) -> str:
    """JSON for jsonb columns. Raw UTF-8, never backslash-u escapes: the server encoding is SQL_ASCII
    and jsonb rejects non-ASCII escape sequences there while passing raw bytes through."""
    return json.dumps(obj, default=str, ensure_ascii=False)


class AnalystError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


class BudgetExceeded(RuntimeError):
    pass


# -- read-only database access ---------------------------------------------------

_analyst_pool: ConnectionPool | None = None


def _pool() -> ConnectionPool:
    """Dedicated stocks_analyst pool when its password is configured, else the app pool."""
    global _analyst_pool
    if not PG_ANALYST_PASSWORD:
        return db.pool
    if _analyst_pool is None:
        conninfo = (f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_ANALYST_USER} "
                    f"password={PG_ANALYST_PASSWORD} connect_timeout=10 client_encoding=UTF8 "
                    f"options=-csearch_path=intel,public")
        _analyst_pool = ConnectionPool(conninfo, min_size=0, max_size=2, open=True,
                                       configure=lambda c: c.adapters.register_loader("numeric", FloatLoader),
                                       kwargs={"row_factory": dict_row})
    return _analyst_pool


def query_ro(sql: str, params: dict) -> list[dict]:
    """One SELECT in a read-only transaction with a 5 s statement timeout, whichever role runs it."""
    with _pool().connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL statement_timeout = '5s'")
                cur.execute(sql, params)
                return cur.fetchall() if cur.description else []


# -- config and prompts ------------------------------------------------------------

def load_prompts(keys: tuple[str, ...] = DAILY_PROMPT_KEYS) -> tuple[dict, str]:
    """All prompt texts, plus a hash over the keys one pass actually uses."""
    texts = {k: (APP_ROOT / "prompts" / f).read_text(encoding="utf-8") for k, f in PROMPT_FILES.items()}
    h = hashlib.sha256("\n".join(texts[k] for k in sorted(keys)).encode("utf-8")).hexdigest()[:16]
    return texts, h


def config_values() -> dict:
    rows = db.rows("SELECT key, value FROM config WHERE key LIKE 'analyst_%'")
    return {r["key"]: r["value"] for r in rows}


def spent_today() -> float:
    row = db.one("SELECT coalesce(sum(cost_usd), 0) AS spent FROM analyses "
                 "WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'")
    return float(row["spent"] if row else 0.0)


def spend_summary() -> dict:
    """Today's analyst spend vs the daily cap (ET day boundary), for the UI counter."""
    row = db.one(
        "SELECT coalesce(sum(cost_usd), 0) AS spent, count(*) AS n, "
        "       to_char((now() AT TIME ZONE 'America/New_York')::date, 'YYYY-MM-DD') AS as_of "
        "FROM analyses "
        "WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York'")
    spent = float(row["spent"]) if row else 0.0
    count = int(row["n"]) if row else 0
    cap = float(config_values().get("analyst_daily_cap_usd") or 0) or None
    remaining = round(max(0.0, cap - spent), 4) if cap else None
    pct = round(spent / cap, 4) if cap else None
    return {"as_of": row["as_of"] if row else None, "spent": round(spent, 4), "cap": cap,
            "count": count, "remaining": remaining, "pct_used": pct}


def spend_detail() -> dict:
    """spend_summary plus today's per-pass rows, for the click-through spend detail view."""
    summary = spend_summary()
    summary["items"] = db.rows(
        "SELECT id, mode, model, status, cost_usd, tokens_in, tokens_out, turns, tool_calls, "
        "       created_at, finished_at "
        "FROM analyses "
        "WHERE created_at >= date_trunc('day', now() AT TIME ZONE 'America/New_York') AT TIME ZONE 'America/New_York' "
        "ORDER BY created_at DESC")
    return summary


DEFAULT_EFFORT = {"plan": "low", "gather": "medium", "draft": "medium", "critic": "low"}
DEFAULT_PROVIDER_PREFS = {"sort": "throughput"}   # OpenRouter routing; data_collection=deny is always added
DEFAULT_CRITIC_FALLBACK = "anthropic/claude-haiku-4.5"


def _model_for(model_ref: str, temperature: float = 0.2, effort: str | None = None, prefs: dict | None = None):
    """'<provider>:<model>' or a bare OpenRouter id -> LangChain chat model (see providers.py)."""
    return providers.chat_model_for(model_ref, temperature=temperature, reasoning_effort=effort, provider_prefs=prefs)


def reap_orphans() -> int:
    """Runs live only inside this process. After a restart any pending or running row is dead; say so."""
    n = db.execute("UPDATE analyses SET status = 'error', error = 'interrupted: the app restarted mid-run', "
                   "finished_at = now() WHERE status IN ('pending', 'running')")
    if n:
        log.warning("marked %d orphaned analysis row(s) as interrupted", n)
    return n


# -- public API ------------------------------------------------------------------

def start_analysis(as_of: date, mode: str = "daily", question: str | None = None,
                   experiment: str | None = None, model_override: str | None = None,
                   actor: str = "ui") -> dict:
    """Create the pending analyses row and hand the run to the worker. Returns the row id."""
    report = db.one("SELECT run_id FROM reports WHERE as_of = %s", (as_of,))
    if not report:
        raise AnalystError(404, f"No report for {as_of}")
    cfg = config_values()
    cap = float(cfg.get("analyst_daily_cap_usd") or 0)
    spent = spent_today()
    if cap and spent >= cap:
        raise AnalystError(429, f"daily analyst cap reached: spent {spent:.2f} of {cap:.2f} USD")
    key = f"{as_of}:{mode}:{experiment or ''}:{model_override or ''}"
    with _lock:
        if key in _inflight:
            return {"analysis_id": _inflight[key], "status": "running", "already_running": True}
        model_id = model_override or str(cfg.get("analyst_model") or "anthropic/claude-sonnet-5")
        try:
            providers.chat_model_for(model_id)
        except Exception as e:  # noqa: BLE001 - surface a missing key or provider before creating a row
            raise AnalystError(503, f"model '{model_id}' unavailable: {e}") from e
        prompts, prompt_hash = load_prompts()
        with db.tx(actor) as cur:
            cur.execute(
                "INSERT INTO analyses (as_of, run_id, model, prompt_version, prompt_hash, status, mode, experiment) "
                "VALUES (%s, %s, %s, %s, %s, 'pending', %s, %s) RETURNING id",
                (as_of, report["run_id"], model_id, str(cfg.get("analyst_prompt_version") or "v1"),
                 prompt_hash, mode, experiment))
            analysis_id = cur.fetchone()["id"]
        _inflight[key] = analysis_id
    # A production daily pass hands off to the entry-watch pass when it finishes.
    chain = mode == "daily" and not experiment and not model_override and bool(cfg.get("entry_signal_auto", True))
    _executor.submit(_run, analysis_id, key, str(as_of), mode, question, model_id, cfg, prompts, cap, spent, chain)
    return {"analysis_id": analysis_id, "status": "pending"}


def _models(model_id: str, cfg: dict) -> dict:
    """Per-step chat models plus critic and fallback, from config."""
    effort = {**DEFAULT_EFFORT, **(cfg.get("analyst_reasoning") or {})}   # step -> low|medium|high|none
    prefs = cfg.get("analyst_provider_prefs", DEFAULT_PROVIDER_PREFS) or None
    cheap_id = cfg.get("analyst_cheap_model")
    fb_id = cfg.get("analyst_critic_fallback_model", DEFAULT_CRITIC_FALLBACK)
    return {
        "model": _model_for(model_id, effort=effort.get("gather"), prefs=prefs),
        "step_models": {"plan": _model_for(model_id, effort=effort.get("plan"), prefs=prefs),
                        "draft": _model_for(model_id, effort=effort.get("draft"), prefs=prefs)},
        "critic": _model_for(str(cheap_id), temperature=0.0, effort=effort.get("critic"), prefs=prefs) if cheap_id else None,
        "critic_fallback": (_model_for(str(fb_id), temperature=0.0, effort=effort.get("critic"), prefs=prefs)
                            if fb_id and fb_id != cheap_id else None),
    }


def _run(analysis_id: int, key: str, as_of: str, mode: str, question: str | None, model_id: str,
         cfg: dict, prompts: dict, cap: float, spent_before: float, chain: bool = False) -> None:
    try:
        db.execute("UPDATE analyses SET status = 'running' WHERE id = %s", (analysis_id,))
        m = _models(model_id, cfg)
        share = bool(cfg.get("analyst_share_positions_offsite", False))
        tools = ToolSet(query_ro, as_of, share_positions=share)

        def on_turn(stats: dict) -> None:
            if cap and spent_before + float(stats.get("cost_usd") or 0) >= cap:
                raise BudgetExceeded(f"daily cap {cap:.2f} USD reached mid-run")

        effects = {
            "load_brief": lambda d: (db.one("SELECT markdown FROM reports WHERE as_of = %s", (d,)) or {}).get("markdown"),
            "hitl_enabled": lambda: bool(cfg.get("analyst_hitl", False)) and mode == "deep",
            "persist": lambda payload: _persist(analysis_id, model_id, payload),
            "on_turn": on_turn,
        }
        graph = build_analyst_graph(m["model"], tools, effects, prompts, critic=m["critic"],
                                    step_models=m["step_models"], critic_fallback=m["critic_fallback"])
        graph.invoke({"as_of": as_of, "mode": mode, "question": question},
                     {"recursion_limit": 60, "run_name": f"analyst:{as_of}", "tags": ["stock-intel", mode]})
    except BudgetExceeded as e:
        _fail(analysis_id, "capped", str(e))
    except Exception as e:  # noqa: BLE001 - the row must never stay pending
        log.exception("analysis %s failed", analysis_id)
        _fail(analysis_id, "error", f"{type(e).__name__}: {str(e)[:500]}")
    finally:
        with _lock:
            _inflight.pop(key, None)
    if chain:
        try:
            out = start_entry_signals(actor="auto")
            log.info("entry signals queued after daily analysis %s: %s", analysis_id, out.get("analyses"))
        except AnalystError as e:
            log.info("entry signals not queued after analysis %s: %s", analysis_id, e.detail)
        except Exception:  # noqa: BLE001 - the chained pass must never mark the daily one failed
            log.exception("entry signal hand-off failed after analysis %s", analysis_id)


# -- entry watch pass ---------------------------------------------------------------

def entry_tickers(only: list[str] | None = None) -> list[str]:
    """Tickers on the entry watch list that are still live (watching or triggered)."""
    rows = db.rows("SELECT ticker FROM entry_watch WHERE status = ANY(%s) ORDER BY ticker",
                   (list(ENTRY_STATUSES_LIVE),))
    live = [r["ticker"] for r in rows]
    if only:
        want = {t.upper() for t in only}
        live = [t for t in live if t in want]
    return live


def entry_as_of() -> date:
    """The session the evidence is read at: the latest scored day, else the latest price day, else today."""
    row = db.one("SELECT greatest((SELECT max(as_of) FROM scores_daily), (SELECT max(as_of) FROM prices_daily)) AS d")
    return (row or {}).get("d") or date.today()


def entry_inflight() -> list[int]:
    with _lock:
        return [v for k, v in _inflight.items() if k.startswith("entry:")]


def start_entry_signals(tickers: list[str] | None = None, model_override: str | None = None,
                        actor: str = "ui") -> dict:
    """Queue one entry-signal pass per batch of live entry-watch tickers. Returns the analyses ids."""
    if tickers is None:
        ensure_holding_rows(actor)
    syms = entry_tickers(tickers)
    if not syms:
        raise AnalystError(404, "no live entry-watch tickers to signal")
    cfg = config_values() | {r["key"]: r["value"] for r in
                             db.rows("SELECT key, value FROM config WHERE key LIKE 'entry_%'")}
    cap = float(cfg.get("analyst_daily_cap_usd") or 0)
    spent = spent_today()
    if cap and spent >= cap:
        raise AnalystError(429, f"daily analyst cap reached: spent {spent:.2f} of {cap:.2f} USD")
    model_id = model_override or cfg.get("entry_signal_model") or str(cfg.get("analyst_model") or "anthropic/claude-sonnet-5")
    model_id = str(model_id)
    try:
        providers.chat_model_for(model_id)
    except Exception as e:  # noqa: BLE001 - surface a missing key or provider before creating rows
        raise AnalystError(503, f"model '{model_id}' unavailable: {e}") from e
    batch = max(1, min(int(cfg.get("entry_signal_batch") or 8), 8))
    as_of = entry_as_of()
    prompts, prompt_hash = load_prompts(ENTRY_PROMPT_KEYS)
    queued: list[int] = []
    skipped: list[str] = []
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        key = "entry:" + ",".join(chunk)
        with _lock:
            if key in _inflight:
                skipped.extend(chunk)
                continue
            with db.tx(actor) as cur:
                cur.execute(
                    "INSERT INTO analyses (as_of, model, prompt_version, prompt_hash, status, mode, experiment) "
                    "VALUES (%s, %s, %s, %s, 'pending', 'entry', %s) RETURNING id",
                    (as_of, model_id, str(cfg.get("analyst_prompt_version") or "v1"), prompt_hash,
                     None if actor in ("ui", "auto", "n8n") else actor))
                analysis_id = cur.fetchone()["id"]
            _inflight[key] = analysis_id
        queued.append(analysis_id)
        _executor.submit(_run_entry, analysis_id, key, str(as_of), chunk, model_id, cfg, prompts, cap, spent)
    return {"analyses": queued, "tickers": syms, "already_running": skipped, "as_of": str(as_of),
            "status": "pending" if queued else "running"}


def _run_entry(analysis_id: int, key: str, as_of: str, tickers: list[str], model_id: str,
               cfg: dict, prompts: dict, cap: float, spent_before: float) -> None:
    try:
        db.execute("UPDATE analyses SET status = 'running' WHERE id = %s", (analysis_id,))
        m = _models(model_id, cfg)
        share = bool(cfg.get("analyst_share_positions_offsite", False))
        tools = ToolSet(query_ro, as_of, share_positions=share)

        def on_turn(stats: dict) -> None:
            if cap and spent_before + float(stats.get("cost_usd") or 0) >= cap:
                raise BudgetExceeded(f"daily cap {cap:.2f} USD reached mid-run")

        effects = {
            "load_brief": lambda d: None,
            "hitl_enabled": lambda: False,
            "persist": lambda payload: _persist_entry(analysis_id, model_id, payload, tickers),
            "on_turn": on_turn,
        }
        graph = build_analyst_graph(m["model"], tools, effects, prompts, critic=m["critic"],
                                    step_models=m["step_models"], critic_fallback=m["critic_fallback"],
                                    profile=ENTRY_PROFILE)
        graph.invoke({"as_of": as_of, "mode": "entry", "question": "entry signals for the watch list",
                      "focus_tickers": tickers},
                     {"recursion_limit": 60, "run_name": f"entry:{as_of}", "tags": ["stock-intel", "entry"]})
    except BudgetExceeded as e:
        _fail(analysis_id, "capped", str(e))
    except Exception as e:  # noqa: BLE001 - the row must never stay pending
        log.exception("entry pass %s failed", analysis_id)
        _fail(analysis_id, "error", f"{type(e).__name__}: {str(e)[:500]}")
    finally:
        with _lock:
            _inflight.pop(key, None)


def _persist_entry(analysis_id: int, model_id: str, payload: dict, tickers: list[str]) -> int:
    """analyses row plus one entry_signals row per ticker the model signalled. Status moves follow."""
    _persist(analysis_id, model_id, payload, kind="signal", cites_fn=cite_ids_signals)
    a = payload.get("analysis") or {}
    signals = a.get("signals") or []
    if not signals:
        return analysis_id
    ev = payload.get("evidence") or {}
    stats = payload.get("stats") or {}
    cost = stats.get("cost_usd")
    share = (float(cost) / len(signals)) if cost else None
    # Add rows: the zone top is capped below the cost basis. Read locally, never from the model's view.
    caps = add_zone_caps([s["ticker"] for s in signals])
    with db.tx("analyst") as cur:
        for s in signals:
            t = s["ticker"]
            entry_ev = (ev.get(f"db:entry:{t}") or {}).get("payload") or {}
            cur.execute(
                "INSERT INTO entry_signals (ticker, as_of, signal, confidence, summary, rationale, invalidation, "
                "  zone_check, suggested_zone, suggested_invalidation, watch_for, rule, ref_price, model, analysis_id, cost_usd) "
                "VALUES (%s, %s::date, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)",
                (t, payload["as_of"], s["signal"], s.get("confidence"), s.get("summary"),
                 _j(jsonable(s.get("rationale") or [])), _j(jsonable(s.get("invalidation") or {})),
                 s.get("zone_check"), _j(jsonable(s.get("suggested_zone"))) if s.get("suggested_zone") else None,
                 s.get("suggested_invalidation"),
                 _j(s.get("watch_for") or []), _j(jsonable(entry_ev.get("rule"))) if entry_ev.get("rule") else None,
                 entry_ev.get("close"), model_id, analysis_id, share))
            # A row added without a zone takes the model's grounded proposal, from a clean pass only;
            # an invalid pass leaves it on the signal row for the Apply button. A user zone is never touched.
            pz = proposed_zone(s, entry_ev, cap=caps.get(t)) if payload.get("status") == "ok" else None
            if pz:
                cur.execute(
                    "UPDATE entry_watch SET zone_low = %s, zone_high = %s, invalidation = COALESCE(invalidation, %s), "
                    "  zone_source = 'analyst' "
                    "WHERE ticker = %s AND zone_low IS NULL AND zone_high IS NULL",
                    (pz["zone_low"], pz["zone_high"], pz["invalidation"], t))
            # enter moves watching -> triggered; anything else moves triggered back. entered/dropped are hand-set.
            if s["signal"] == "enter":
                cur.execute("UPDATE entry_watch SET status = 'triggered' WHERE ticker = %s AND status = 'watching'", (t,))
            else:
                cur.execute("UPDATE entry_watch SET status = 'watching' WHERE ticker = %s AND status = 'triggered'", (t,))
    return analysis_id


def add_zone_caps(tickers: list[str]) -> dict[str, float]:
    """ticker -> highest zone top for add rows (avg cost less entry_dca_min_discount)."""
    if not tickers:
        return {}
    disc = db.one("SELECT value FROM config WHERE key = 'entry_dca_min_discount'")
    try:
        discount = float(disc["value"]) if disc else 0.02
    except (TypeError, ValueError):
        discount = 0.02
    rows = db.rows("SELECT e.ticker, p.avg_cost FROM entry_watch e JOIN positions p ON p.ticker = e.ticker "
                   "WHERE e.kind = 'add' AND e.ticker = ANY(%s)", (tickers,))
    out = {}
    for r in rows:
        cap = add_zone_cap(r["avg_cost"], discount)
        if cap is not None:
            out[r["ticker"]] = cap
    return out


def ensure_holding_rows(actor: str = "auto") -> list[str]:
    """Enrol every open position as an add row when entry_watch_holdings is true. Returns new tickers."""
    flag = db.one("SELECT value FROM config WHERE key = 'entry_watch_holdings'")
    if flag is not None and flag["value"] is False:
        return []
    with db.tx(actor) as cur:
        cur.execute(
            "INSERT INTO entry_watch (ticker, kind, status, horizon, thesis, added_by, zone_source) "
            "SELECT p.ticker, 'add', 'watching', 'weeks', 'Holding: add on a dip to lower the average cost.', %s, 'user' "
            "FROM positions p WHERE NOT EXISTS (SELECT 1 FROM entry_watch e WHERE e.ticker = p.ticker) "
            "RETURNING ticker", (actor,))
        new = [r["ticker"] for r in cur.fetchall()]
        # A holding that was watched as a new position is an add row from now on.
        cur.execute("UPDATE entry_watch e SET kind = 'add' WHERE e.kind = 'new' "
                    "AND EXISTS (SELECT 1 FROM positions p WHERE p.ticker = e.ticker)")
    return new


def latest_signal_rows() -> list[dict]:
    """Newest entry_signals row per ticker."""
    return db.rows(
        "SELECT DISTINCT ON (ticker) id, ticker, as_of, signal, confidence, summary, rationale, invalidation, "
        "       zone_check, suggested_zone, suggested_invalidation, watch_for, rule, ref_price, model, analysis_id, "
        "       cost_usd, created_at "
        "FROM entry_signals ORDER BY ticker, created_at DESC, id DESC")


def _fail(analysis_id: int, status: str, error: str) -> None:
    db.execute("UPDATE analyses SET status = %s, error = %s, finished_at = now() WHERE id = %s",
               (status, error, analysis_id))


def _persist(analysis_id: int, model_id: str, payload: dict, kind: str = "analyze", cites_fn=cite_ids_draft) -> int:
    """Final write: analyses row, one analysis_sources row per cited id, a runs row of the given kind."""
    stats = payload.get("stats") or {}
    a = payload.get("analysis")
    cost = stats.get("cost_usd")
    pid, mid = providers.parse_ref(model_id)
    if not stats.get("cost_known") and pid == providers.DEFAULT_PROVIDER:
        cost = openrouter.estimate_cost(mid if "/" in mid else f"anthropic/{mid}", stats.get("tokens_in", 0), stats.get("tokens_out", 0))
        stats = dict(stats, cost_usd=cost)
        payload["stats"] = stats
    output = None
    if a is not None:
        output = dict(a, review=payload.get("report"), plan=payload.get("plan"))
    ev = payload.get("evidence") or {}
    cited = cites_fn(a) if a else set()
    with db.tx("analyst") as cur:
        cur.execute(
            "UPDATE analyses SET status = %s, output = %s::jsonb, markdown = %s, error = %s, tokens_in = %s, "
            "tokens_out = %s, cost_usd = %s, turns = %s, tool_calls = %s, fetches = 0, stripped = %s, "
            "finished_at = now() WHERE id = %s",
            (payload["status"], _j(jsonable(output)) if output is not None else None,
             payload.get("markdown"), payload.get("error") or _first_issue(payload), stats.get("tokens_in"),
             stats.get("tokens_out"), cost, stats.get("turns"), stats.get("tool_calls"),
             (payload.get("report") or {}).get("stripped"), analysis_id))
        cur.execute("DELETE FROM analysis_sources WHERE analysis_id = %s", (analysis_id,))
        for cid in sorted(cited):
            item = ev.get(cid)
            if not item:
                continue
            cur.execute(
                "INSERT INTO analysis_sources (analysis_id, ref, kind, url, domain, published_at, excerpt, payload) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)",
                (analysis_id, cid, item["kind"], (item["payload"] or {}).get("url") if isinstance(item["payload"], dict) else None,
                 None, _published(item), item["excerpt"], _j(jsonable(item["payload"]))))
        cur.execute(
            "INSERT INTO runs (run_id, kind, as_of, started_at, finished_at, status, summary) "
            "VALUES (%s, %s, %s::date, now() - make_interval(secs => %s), now(), %s, %s::jsonb)",
            (str(uuid.uuid4()), kind, payload["as_of"], float(stats.get("wall_seconds") or 0), payload["status"],
             _j({"analysis_id": analysis_id, "model": model_id, "turns": stats.get("turns"),
                         "tool_calls": stats.get("tool_calls"), "tokens_in": stats.get("tokens_in"),
                         "tokens_out": stats.get("tokens_out"), "cost_usd": cost,
                         "stripped": (payload.get("report") or {}).get("stripped"), "mode": payload.get("mode")})))
    return analysis_id


def _published(item: dict) -> str | None:
    p = item.get("payload")
    return p.get("published_at") if isinstance(p, dict) else None


def _first_issue(payload: dict) -> str | None:
    if payload.get("status") in ("ok", "discarded"):
        return None
    issues = (payload.get("report") or {}).get("issues") or []
    return "; ".join(issues[:3])[:500] or None


# -- reads for the API -------------------------------------------------------------

def get_analysis(analysis_id: int) -> dict | None:
    row = db.one("SELECT id, as_of, run_id, model, prompt_version, prompt_hash, status, mode, experiment, output, "
                 "markdown, error, tokens_in, tokens_out, cost_usd, turns, tool_calls, stripped, created_at, "
                 "finished_at FROM analyses WHERE id = %s", (analysis_id,))
    if not row:
        return None
    row["sources"] = db.rows("SELECT ref, kind, url, published_at, excerpt, payload FROM analysis_sources "
                             "WHERE analysis_id = %s ORDER BY ref", (analysis_id,))
    row["feedback"] = db.rows("SELECT id, rating, note, created_at FROM analysis_feedback "
                              "WHERE analysis_id = %s ORDER BY created_at DESC", (analysis_id,))
    return row
