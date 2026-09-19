"""Analyst tools over an injected read-only query function.

Every tool returns a list of evidence items: {id, kind, ref, excerpt, payload, ticker}.
The graph formats them for the model and keeps them for citation checks. No DB, env or
network access here; `query_fn(sql, params) -> list[dict]` is supplied by the caller.
"""

from __future__ import annotations

import ast
import json
import operator
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from ..entries import rule_signal

QueryFn = Callable[[str, dict], list[dict]]

ROW_CAP = 500          # rows any single query may return
MODEL_ROW_CAP = 60     # rows shown to the model per evidence item
TEMPLATE_CALL_CAP = 30
SQL_CALL_CAP = 5

# Fields that count as price levels for number grounding.
LEVEL_KEYS = {"close", "open", "high", "low", "sma20", "sma50", "sma200", "hi52w", "lo52w",
              "target_entry", "avg_cost", "prior_20d_high", "entry_low", "entry_high", "value",
              "zone_low", "zone_high", "invalidation"}

POSITION_FIELDS = ("qty", "avg_cost", "cost_basis", "market_value", "weight", "notes")


def jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    return v


def _ev(id_: str, payload: Any, excerpt: str, ticker: str | None = None, kind: str = "db") -> dict:
    return {"id": id_, "kind": kind, "ref": id_, "excerpt": excerpt[:300],
            "payload": jsonable(payload), "ticker": ticker}


def _fmt(v: Any, dp: int = 2) -> str:
    if v is None:
        return "-"
    if isinstance(v, (int, float, Decimal)):
        return f"{float(v):.{dp}f}"
    return str(v)


# ---------------------------------------------------------------------------
# SQL templates
# ---------------------------------------------------------------------------

SQL = {
    "watchlist": """
        SELECT w.ticker, w.name, w.tags, w.target_entry, w.notes,
               (p.ticker IS NOT NULL) AS has_position
        FROM watchlist w LEFT JOIN positions p ON p.ticker = w.ticker
        WHERE w.active ORDER BY w.ticker LIMIT 500""",
    "scores_for_date": """
        SELECT s.ticker, w.name, w.tags, s.momentum, s.value, s.sentiment, s.catalyst, s.composite,
               s.flags, s.risks, s.reasons, p.close, i.rsi14, i.ret_1d, i.ret_5d, i.ret_20d, i.vol_z20,
               i.sma20, i.sma50, i.sma200, i.hi52w, i.lo52w, i.pct_from_hi52w, w.target_entry
        FROM scores_daily s
        LEFT JOIN watchlist w ON w.ticker = s.ticker
        LEFT JOIN prices_daily p ON p.ticker = s.ticker AND p.as_of = s.as_of
        LEFT JOIN indicators_daily i ON i.ticker = s.ticker AND i.as_of = s.as_of
        WHERE s.as_of = %(as_of)s::date ORDER BY s.composite DESC NULLS LAST LIMIT 500""",
    "score_history": """
        SELECT as_of, composite, momentum, sentiment, flags, risks
        FROM scores_daily WHERE ticker = %(ticker)s AND as_of <= %(as_of)s::date
        ORDER BY as_of DESC LIMIT %(days)s""",
    "price_window": """
        SELECT p.as_of, p.open, p.high, p.low, p.close, p.volume,
               i.sma20, i.sma50, i.sma200, i.rsi14, i.atr14, i.vol_z20, i.hi52w, i.lo52w,
               i.pct_from_hi52w, i.ret_1d, i.ret_5d, i.ret_20d
        FROM prices_daily p LEFT JOIN indicators_daily i ON i.ticker = p.ticker AND i.as_of = p.as_of
        WHERE p.ticker = %(ticker)s AND p.as_of <= %(as_of)s::date
        ORDER BY p.as_of DESC LIMIT %(days)s""",
    "news_for_ticker": """
        SELECT id, headline, source, url, published_at, sentiment, sentiment_src, summary
        FROM news_articles
        WHERE ticker = %(ticker)s
          AND published_at >= (%(as_of)s::date - %(days)s * interval '1 day')
          AND published_at < (%(as_of)s::date + interval '1 day')
        ORDER BY published_at DESC LIMIT %(limit)s""",
    "holdings": """
        SELECT pos.ticker, w.name, w.tags, pos.qty, pos.avg_cost, pos.opened_at, pos.notes,
               p.close AS close, p.as_of AS price_as_of,
               (pos.qty * pos.avg_cost) AS cost_basis,
               (pos.qty * p.close) AS market_value,
               i.sma20, i.sma50, i.sma200, i.rsi14, i.ret_1d, i.ret_5d, i.ret_20d
        FROM positions pos
        JOIN watchlist w ON w.ticker = pos.ticker
        LEFT JOIN LATERAL (SELECT * FROM prices_daily x WHERE x.ticker = pos.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) p ON true
        LEFT JOIN LATERAL (SELECT * FROM indicators_daily x WHERE x.ticker = pos.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) i ON true
        ORDER BY market_value DESC NULLS LAST LIMIT 500""",
    "flag_history": """
        SELECT as_of, flags, risks, composite FROM scores_daily
        WHERE ticker = %(ticker)s AND as_of <= %(as_of)s::date
          AND (cardinality(flags) > 0 OR cardinality(risks) > 0)
        ORDER BY as_of DESC LIMIT %(days)s""",
    "run_footer": """
        SELECT r.run_id, r.kind, r.status, r.started_at, r.finished_at, r.summary,
               (SELECT jsonb_object_agg(source, cnt) FROM (
                    SELECT source, jsonb_build_object(
                        'ok', count(*) FILTER (WHERE status = 'ok'),
                        'error', count(*) FILTER (WHERE status = 'error'),
                        'quota_skip', count(*) FILTER (WHERE status = 'quota_skip')) AS cnt
                    FROM api_usage u WHERE u.run_id = r.run_id GROUP BY source) x) AS sources
        FROM runs r
        WHERE r.kind = 'daily'
          AND (r.run_id = (SELECT run_id FROM reports WHERE as_of = %(as_of)s::date) OR r.as_of = %(as_of)s::date)
        ORDER BY (r.run_id = (SELECT run_id FROM reports WHERE as_of = %(as_of)s::date)) DESC, r.started_at DESC
        LIMIT 1""",
    "benchmarks": """
        SELECT w.ticker, w.name, p.close, i.ret_1d, i.ret_5d, i.ret_20d, i.rsi14, i.sma50, i.sma200
        FROM watchlist w
        LEFT JOIN prices_daily p ON p.ticker = w.ticker AND p.as_of = %(as_of)s::date
        LEFT JOIN indicators_daily i ON i.ticker = w.ticker AND i.as_of = %(as_of)s::date
        WHERE w.tags @> ARRAY['benchmark'] ORDER BY w.ticker LIMIT 20""",
    "earnings": """
        SELECT report_date, hour, eps_est, eps_actual, rev_est, rev_actual, surprise_pct
        FROM earnings_calendar WHERE ticker = %(ticker)s
          AND report_date BETWEEN (%(as_of)s::date - 90) AND (%(as_of)s::date + 60)
        ORDER BY report_date DESC LIMIT 8""",
    "fundamentals": """
        SELECT as_of, market_cap, pe, ps, pb, ev_ebitda, ev_rev, fcf_yield, rev_growth_yoy, rev_growth_q_yoy,
               eps_growth_yoy, gross_margin, op_margin, net_margin, roe, debt_equity, current_ratio, beta,
               avg_vol_10d, cash, debt, burn_qtr, runway_qtrs, source
        FROM fundamentals WHERE ticker = %(ticker)s AND as_of <= %(as_of)s::date
        ORDER BY as_of DESC LIMIT 1""",
    "insider": """
        SELECT filed_at, tx_date, insider, tx_type, code, derivative, shares, price, value FROM insider_tx
        WHERE ticker = %(ticker)s AND filed_at BETWEEN (%(as_of)s::date - %(days)s) AND %(as_of)s::date
        ORDER BY filed_at DESC LIMIT 40""",
    "recs": """
        SELECT as_of, strong_buy, buy, hold, sell, strong_sell FROM analyst_recs
        WHERE ticker = %(ticker)s AND as_of <= %(as_of)s::date
        ORDER BY as_of DESC LIMIT 6""",
    "entry_watch": """
        SELECT e.ticker, w.name, e.thesis, e.zone_low, e.zone_high, e.invalidation, e.horizon, e.status,
               e.zone_source, e.kind, e.notes, e.added_at,
               pos.avg_cost, pos.qty,
               p.close, p.as_of AS price_as_of,
               i.sma20, i.sma50, i.sma200, i.rsi14, i.vol_z20, i.hi52w, i.lo52w, i.ret_5d, i.ret_20d,
               sd.news_score, sd.news_count,
               s.composite, s.flags, s.risks
        FROM entry_watch e
        JOIN watchlist w ON w.ticker = e.ticker
        LEFT JOIN LATERAL (SELECT * FROM prices_daily x WHERE x.ticker = e.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) p ON true
        LEFT JOIN LATERAL (SELECT * FROM indicators_daily x WHERE x.ticker = e.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) i ON true
        LEFT JOIN LATERAL (SELECT * FROM sentiment_daily x WHERE x.ticker = e.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) sd ON true
        LEFT JOIN LATERAL (SELECT * FROM scores_daily x WHERE x.ticker = e.ticker
                             AND x.as_of <= %(as_of)s::date ORDER BY x.as_of DESC LIMIT 1) s ON true
        LEFT JOIN positions pos ON pos.ticker = e.ticker
        WHERE e.status IN ('watching', 'triggered') ORDER BY e.ticker LIMIT 200""",
}


# ---------------------------------------------------------------------------
# Guarded free-form SELECT
# ---------------------------------------------------------------------------

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|grant|revoke|truncate|copy|vacuum|analyze|"
    r"call|do|execute|listen|notify|lock|refresh|reindex|cluster|comment|security|set|reset|"
    r"show|pg_sleep|pg_read|pg_ls|lo_|dblink|current_setting|set_config)\b", re.I)
_SCHEMA_REF = re.compile(r"\b(pg_catalog|information_schema|pg_[a-z_]+)\b", re.I)
_LIMIT = re.compile(r"\blimit\s+(\d+)\s*$", re.I)


class SqlRejected(ValueError):
    pass


def guard_select(sql: str, row_cap: int = ROW_CAP) -> str:
    """Accept one SELECT (or WITH ... SELECT) over intel and force a LIMIT. Anything else raises."""
    s = (sql or "").strip().rstrip(";").strip()
    if not s:
        raise SqlRejected("empty statement")
    if ";" in s:
        raise SqlRejected("one statement only")
    if "--" in s or "/*" in s:
        raise SqlRejected("comments are not allowed")
    if not re.match(r"^(select|with)\b", s, re.I):
        raise SqlRejected("only SELECT is allowed")
    if _FORBIDDEN.search(s):
        raise SqlRejected("statement contains a forbidden keyword")
    if _SCHEMA_REF.search(s):
        raise SqlRejected("system catalogs are not readable")
    if re.search(r"\b\w+\.\w+\.\w+\b", s):
        raise SqlRejected("no cross-database references")
    m = _LIMIT.search(s)
    if m:
        if int(m.group(1)) > row_cap:
            s = s[: m.start()] + f"LIMIT {row_cap}"
    else:
        s = f"{s} LIMIT {row_cap}"
    return s


# ---------------------------------------------------------------------------
# Safe arithmetic
# ---------------------------------------------------------------------------

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Pow: operator.pow, ast.USub: operator.neg, ast.UAdd: operator.pos, ast.Mod: operator.mod}
_FUNCS = {"abs": abs, "round": lambda x, nd=0: round(x, int(nd)), "min": min, "max": max}


def safe_calc(expr: str) -> float:
    """Evaluate + - * / ** % and abs/round/min/max over numbers. Nothing else parses."""
    tree = ast.parse(expr.strip(), mode="eval")

    def ev(n: ast.AST) -> float:
        if isinstance(n, ast.Expression):
            return ev(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.BinOp) and type(n.op) in _OPS:
            if isinstance(n.op, ast.Pow) and abs(ev(n.right)) > 64:
                raise ValueError("exponent too large")
            return _OPS[type(n.op)](ev(n.left), ev(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in _OPS:
            return _OPS[type(n.op)](ev(n.operand))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return float(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        raise ValueError(f"unsupported expression: {ast.dump(n)[:60]}")

    return ev(tree)


# ---------------------------------------------------------------------------
# Tool argument schemas
# ---------------------------------------------------------------------------

class TickerArgs(BaseModel):
    ticker: str = Field(description="Ticker symbol, upper case.")


class TickerDaysArgs(TickerArgs):
    days: int = Field(default=30, ge=1, le=400, description="Trading days back from as_of.")


class NewsArgs(TickerArgs):
    days: int = Field(default=7, ge=1, le=90)
    limit: int = Field(default=10, ge=1, le=40)


class NoArgs(BaseModel):
    pass


class SqlArgs(BaseModel):
    select: str = Field(description="One SELECT over intel tables. Read-only; LIMIT is forced.")


class CalcArgs(BaseModel):
    expr: str = Field(description="Arithmetic only, e.g. (219.32 - 205.1) / 219.32.")


# ---------------------------------------------------------------------------
# ToolSet
# ---------------------------------------------------------------------------

class ToolSet:
    """Database templates, guarded SQL and calc bound to one as_of and one query function."""

    def __init__(self, query_fn: QueryFn, as_of: str, share_positions: bool = False):
        self.q = query_fn
        self.as_of = as_of
        self.share_positions = share_positions
        self.calls: dict[str, int] = {}
        self._sql_n = 0
        self._calc_n = 0
        self.lc_tools = self._build_lc_tools()
        self.caps = {"db_sql": SQL_CALL_CAP, "_templates": TEMPLATE_CALL_CAP}

    # -- baseline pack (code path, not model) ------------------------------
    def baseline(self, extra: tuple[str, ...] = ()) -> list[dict]:
        out: list[dict] = []
        for name in ("watchlist", "benchmarks", "scores_for_date", "holdings", "run_footer") + tuple(extra):
            out.extend(self.run(name, {}))
        return out

    def allowed_tickers(self, evidence: list[dict]) -> list[str]:
        return sorted({e["payload"]["ticker"] for e in evidence
                       if e["id"].startswith("db:watchlist:") and e["payload"].get("ticker")})

    # -- dispatch ----------------------------------------------------------
    def is_template(self, name: str) -> bool:
        return name in SQL

    def run(self, name: str, args: dict) -> list[dict]:
        """Run one tool by name. Raises on a rejected SQL or calc; the graph reports that to the model."""
        self.calls[name] = self.calls.get(name, 0) + 1
        if name == "db_sql":
            return self._db_sql(args["select"])
        if name == "calc":
            return self._calc(args["expr"])
        if name not in SQL:
            raise KeyError(f"unknown tool {name}")
        params = {"as_of": self.as_of, **{k: v for k, v in args.items() if v is not None}}
        if "ticker" in params:
            params["ticker"] = str(params["ticker"]).upper().strip()
        params.setdefault("days", 30)
        params.setdefault("limit", 10)
        rows = self.q(SQL[name], params)[:ROW_CAP]
        return getattr(self, f"_ev_{name}")(rows, params)

    # -- per-template evidence shaping -------------------------------------
    def _ev_watchlist(self, rows, p):
        return [_ev(f"db:watchlist:{r['ticker']}", r,
                    f"{r['ticker']} {r.get('name') or ''} tags={','.join(r.get('tags') or [])} "
                    f"target_entry={_fmt(r.get('target_entry'))} position={'yes' if r.get('has_position') else 'no'}",
                    r["ticker"]) for r in rows]

    def _ev_benchmarks(self, rows, p):
        return [_ev(f"db:bench:{r['ticker']}:{self.as_of}", r,
                    f"{r['ticker']} close={_fmt(r.get('close'))} 1d={_fmt(r.get('ret_1d'), 4)} "
                    f"5d={_fmt(r.get('ret_5d'), 4)} 20d={_fmt(r.get('ret_20d'), 4)} rsi={_fmt(r.get('rsi14'), 1)}",
                    r["ticker"]) for r in rows]

    def _ev_scores_for_date(self, rows, p):
        return [_ev(f"db:scores:{r['ticker']}:{self.as_of}", r,
                    f"{r['ticker']} composite={_fmt(r.get('composite'), 1)} close={_fmt(r.get('close'))} "
                    f"rsi={_fmt(r.get('rsi14'), 1)} flags={','.join(r.get('flags') or []) or '-'} "
                    f"risks={','.join(r.get('risks') or []) or '-'}", r["ticker"]) for r in rows]

    def _ev_holdings(self, rows, p):
        out = []
        for r in rows:
            r = dict(r)
            if not self.share_positions:
                for k in POSITION_FIELDS:
                    r.pop(k, None)
                r["position_redacted"] = True
            ex = f"{r['ticker']} held since {r.get('opened_at') or '?'} close={_fmt(r.get('close'))}"
            if self.share_positions:
                ex += f" qty={_fmt(r.get('qty'), 3)} avg_cost={_fmt(r.get('avg_cost'))}"
            out.append(_ev(f"db:holding:{r['ticker']}", r, ex, r["ticker"]))
        return out

    def _ev_run_footer(self, rows, p):
        if not rows:
            return [_ev(f"db:run:{self.as_of}", {"note": "no daily run row"}, "no daily run for this date")]
        r = rows[0]
        return [_ev(f"db:run:{self.as_of}", r,
                    f"run {r.get('status')} sources={json.dumps(jsonable(r.get('sources')))[:200]}")]

    def _ev_score_history(self, rows, p):
        t = p["ticker"]
        if not rows:
            return [_ev(f"db:score_history:{t}:{self.as_of}", [], f"{t}: no score rows", t)]
        ex = f"{t} composite last {len(rows)}d: " + ", ".join(_fmt(r.get("composite"), 0) for r in rows[:10])
        return [_ev(f"db:score_history:{t}:{self.as_of}", rows[:MODEL_ROW_CAP], ex, t)]

    def _ev_price_window(self, rows, p):
        t = p["ticker"]
        if not rows:
            return [_ev(f"db:prices:{t}:{self.as_of}", {"latest": None, "window": []}, f"{t}: no price rows", t)]
        latest = rows[0]
        ex = (f"{t} close={_fmt(latest.get('close'))} sma20={_fmt(latest.get('sma20'))} "
              f"sma50={_fmt(latest.get('sma50'))} sma200={_fmt(latest.get('sma200'))} "
              f"hi52w={_fmt(latest.get('hi52w'))} lo52w={_fmt(latest.get('lo52w'))} rsi={_fmt(latest.get('rsi14'), 1)}")
        window = [{k: r.get(k) for k in ("as_of", "open", "high", "low", "close", "volume", "rsi14")}
                  for r in rows[:MODEL_ROW_CAP]]
        return [_ev(f"db:prices:{t}:{self.as_of}", {"latest": latest, "window": window}, ex, t)]

    def _ev_news_for_ticker(self, rows, p):
        t = p["ticker"]
        if not rows:
            return [_ev(f"db:news:{t}:0", {"note": "no articles in window"}, f"{t}: no stored articles", t)]
        return [_ev(f"db:news:{t}:{i}", r,
                    f"{str(r.get('published_at'))[:10]} {r.get('source')}: {r.get('headline') or ''} "
                    f"(sent={_fmt(r.get('sentiment'), 2)})", t) for i, r in enumerate(rows, 1)]

    def _ev_flag_history(self, rows, p):
        t = p["ticker"]
        ex = f"{t} flagged on {len(rows)} days: " + "; ".join(
            f"{r['as_of']} {','.join((r.get('flags') or []) + (r.get('risks') or []))}" for r in rows[:6])
        return [_ev(f"db:flags:{t}", rows[:MODEL_ROW_CAP], ex if rows else f"{t}: never flagged", t)]

    def _ev_earnings(self, rows, p):
        t = p["ticker"]
        ex = "; ".join(f"{r['report_date']}{' ' + r['hour'] if r.get('hour') else ''} eps {_fmt(r.get('eps_actual'))}/{_fmt(r.get('eps_est'))}" for r in rows[:3])
        return [_ev(f"db:earnings:{t}", rows, ex or f"{t}: no earnings rows", t)]

    def _ev_fundamentals(self, rows, p):
        t = p["ticker"]
        if not rows:
            return [_ev(f"db:fund:{t}", {}, f"{t}: no fundamentals stored", t)]
        r = rows[0]
        return [_ev(f"db:fund:{t}", r, f"{t} ps={_fmt(r.get('ps'))} ev_rev={_fmt(r.get('ev_rev'))} "
                    f"runway_qtrs={_fmt(r.get('runway_qtrs'), 1)} as_of={r.get('as_of')}", t)]

    def _ev_insider(self, rows, p):
        t = p["ticker"]
        open_mkt = [r for r in rows if not r.get("derivative")]
        buys = sum(1 for r in open_mkt if r.get("tx_type") == "P")
        sells = sum(1 for r in open_mkt if r.get("tx_type") == "S")
        net = sum(float(r.get("value") or 0) * (1 if r.get("tx_type") == "P" else -1) for r in open_mkt if r.get("tx_type") in ("P", "S"))
        return [_ev(f"db:insider:{t}", rows, f"{t} insider tx {len(rows)} ({len(open_mkt)} open-market): {buys} buys, {sells} sells, net ${net:,.0f}", t)]

    def _ev_recs(self, rows, p):
        t = p["ticker"]
        if not rows:
            return [_ev(f"db:recs:{t}", [], f"{t}: no analyst recommendations stored", t)]
        ex = "; ".join(f"{r['as_of']} sb={r.get('strong_buy')} b={r.get('buy')} h={r.get('hold')} s={r.get('sell')} ss={r.get('strong_sell')}" for r in rows[:3])
        return [_ev(f"db:recs:{t}", rows, f"{t} analyst recs by month: {ex}", t)]

    def _ev_entry_watch(self, rows, p):
        out = []
        for r in rows:
            r = dict(r)
            if not self.share_positions:
                # The model never sees the cost basis; the rule read it gets is computed without it too.
                r.pop("avg_cost", None)
                r.pop("qty", None)
                r["position_redacted"] = True
            r["rule"] = rule_signal(r)
            no_zone = r.get("zone_low") is None and r.get("zone_high") is None
            zone = "NO ZONE (propose one)" if no_zone else f"zone {_fmt(r.get('zone_low'))}-{_fmt(r.get('zone_high'))}"
            kind = "ADD to a holding, zone must sit below the cost basis (code clips it)" if r.get("kind") == "add" else "new position"
            ex = (f"{r['ticker']} entry watch ({kind}): {zone} inval={_fmt(r.get('invalidation'))} close={_fmt(r.get('close'))} "
                  f"rule={r['rule']['signal']} ({'; '.join(r['rule']['reasons'][:3])})")
            out.append(_ev(f"db:entry:{r['ticker']}", r, ex, r["ticker"]))
        return out

    # -- free-form and calc -------------------------------------------------
    def _db_sql(self, select: str) -> list[dict]:
        sql = guard_select(select)
        rows = self.q(sql, {})
        self._sql_n += 1
        ex = f"{len(rows)} rows: " + json.dumps(jsonable(rows[:2]), default=str)[:200]
        return [_ev(f"db:sql:{self._sql_n}", {"select": sql, "rows": rows[:MODEL_ROW_CAP], "row_count": len(rows)}, ex)]

    def _calc(self, expr: str) -> list[dict]:
        value = safe_calc(expr)
        self._calc_n += 1
        return [_ev(f"calc:{self._calc_n}", {"expr": expr, "value": value}, f"{expr} = {value:.6g}", kind="calc")]

    # -- LangChain tool objects ----------------------------------------------
    def _build_lc_tools(self) -> list[StructuredTool]:
        def t(name, desc, schema):
            # The graph intercepts calls by name; func is never invoked directly.
            return StructuredTool.from_function(func=lambda **kw: None, name=name, description=desc, args_schema=schema)

        return [
            t("score_history", "Composite, momentum, sentiment, flags and risks per day for one ticker.", TickerDaysArgs),
            t("price_window", "OHLCV plus SMA20/50/200, RSI14, ATR, 52-week high/low for one ticker, latest first.", TickerDaysArgs),
            t("news_for_ticker", "Stored headlines with sentiment for one ticker over the last N days.", NewsArgs),
            t("flag_history", "Days on which one ticker carried an entry flag or a risk flag.", TickerDaysArgs),
            t("earnings", "Earnings dates and surprises near as_of for one ticker.", TickerArgs),
            t("fundamentals", "Latest stored valuation and balance-sheet snapshot for one ticker.", TickerArgs),
            t("insider", "Insider transactions for one ticker in the last N days (SEC Form 4 via Finnhub).", TickerDaysArgs),
            t("recs", "Monthly analyst buy / hold / sell counts for one ticker, latest first.", TickerArgs),
            t("db_sql", "One read-only SELECT over intel tables (prices_daily, indicators_daily, scores_daily, "
                        "news_articles, watchlist, sentiment_daily, macro_daily, fundamentals, earnings_calendar, "
                        "insider_tx, analyst_recs, filings). Use only when no template fits.", SqlArgs),
            t("calc", "Deterministic arithmetic. Use it for every distance, ratio or percent you state.", CalcArgs),
        ]
