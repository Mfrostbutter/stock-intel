"""The analyst graph. Pure factory: models, tools and effects are injected.

    START -> baseline -> plan -> gather -> ground -> draft -> critique -> (draft)* -> hitl -> persist -> END

baseline  code: fixed templates (watchlist, benchmarks, scores, holdings, run footer) into the index
plan      model: which tickers to dig into and which brief claims need a source
gather    model + tools: bounded tool-calling loop over the database tools
ground    code: render the evidence index the draft may cite, nothing else
draft     model: structured AnalysisDraft with a citation on every claim
critique  code checks (citations, levels, allow-list) plus an optional critic model; retries draft once
hitl      interrupt() when effects["hitl_enabled"]() is true
persist   effects["persist"](payload) -> analysis id

Effects: load_brief(as_of) -> str | None, hitl_enabled() -> bool, persist(payload) -> int | None,
         on_turn(stats) (optional, spend check; raise to stop the run).
Models are LangChain chat models (invoke, bind_tools, with_structured_output). Tools are a ToolSet.
"""

from __future__ import annotations

import json
import logging
import operator
import time
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .evidence import cite_ids_draft, evidence_block, render_index, to_markdown, validate_draft
from .schemas import DISCLAIMER, AnalysisDraft, Critique, PlanNotes

log = logging.getLogger("stockintel.analyst.graph")

BUDGETS = {"turns": 25, "wall_seconds": 240, "research_tickers": 8, "max_stripped_ratio": 1 / 3,
           "critic_retry_sleep": 2.0}
PREFETCH = ("score_history", "price_window")   # loaded by code for every research ticker before the tool loop


def _research_daily(state: dict, evidence: dict, allowed: set[str], limit: int) -> list[str]:
    return pick_research(evidence, allowed, limit)


# What differs between passes: which tickers, which draft schema, which checks, which prompts.
DAILY_PROFILE = {
    "name": "daily",
    "research": _research_daily,
    "plan_notes": True,
    "baseline_extra": (),
    "schema": AnalysisDraft,
    "validate": validate_draft,
    "cites": cite_ids_draft,
    "markdown": to_markdown,
    "prompts": {"gather": "gather", "draft": "draft"},
}


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class AnalystState(TypedDict, total=False):
    as_of: str
    mode: str                        # daily | question | deep | entry
    question: str | None
    focus_tickers: list[str]         # entry pass: the tickers under review
    brief: str
    plan: dict
    tickers: list[str]               # allow-list from the watchlist
    evidence: Annotated[dict, _merge]
    priority: list[str]              # evidence ids to render first
    messages: list                   # gather loop transcript
    draft: dict | None
    attempt: int
    report: dict
    analysis: dict | None
    status: str
    capped: bool
    error: str | None
    stats: Annotated[dict, _merge]
    started: float
    decision: dict | None


def _usage(resp: Any) -> dict:
    """Tokens and cost from one model response (OpenRouter puts cost in usage)."""
    u = getattr(resp, "usage_metadata", None) or {}
    meta = getattr(resp, "response_metadata", None) or {}
    tu = meta.get("token_usage") or {}
    # OpenRouter reports cost in usage; org-billed keys can report 0 with the real figure in cost_details.
    # Zero is treated as unknown so the caller falls back to the catalog estimate.
    cost = tu.get("cost")
    details = tu.get("cost_details") if isinstance(tu.get("cost_details"), dict) else {}
    upstream = details.get("upstream_inference_cost")
    if upstream:
        cost = max(float(cost or 0), float(upstream))
    return {"tokens_in": int(u.get("input_tokens") or tu.get("prompt_tokens") or 0),
            "tokens_out": int(u.get("output_tokens") or tu.get("completion_tokens") or 0),
            "cost_usd": float(cost) if cost else None}


def _add_usage(stats: dict, u: dict) -> dict:
    s = dict(stats)
    s["turns"] = s.get("turns", 0) + 1
    s["tokens_in"] = s.get("tokens_in", 0) + u["tokens_in"]
    s["tokens_out"] = s.get("tokens_out", 0) + u["tokens_out"]
    if u["cost_usd"] is not None:
        s["cost_usd"] = round(s.get("cost_usd") or 0.0, 6) + u["cost_usd"]
        s["cost_known"] = True
    return s


def _structured(model, schema, messages) -> tuple[Any, dict]:
    """Structured call returning (parsed, usage). Raises when the model output does not parse."""
    out = model.with_structured_output(schema, include_raw=True).invoke(messages)
    raw, parsed, err = out.get("raw"), out.get("parsed"), out.get("parsing_error")
    if err or parsed is None:
        raise ValueError(f"structured output failed: {err}")
    return parsed, _usage(raw)


def pick_research(evidence: dict, allowed: set[str], limit: int) -> list[str]:
    """Tickers worth a closer look, by rule: held, then entry flags, then risk flags, then largest movers."""
    scores = [e for k, e in evidence.items() if k.startswith("db:scores:")]
    held = [e["ticker"] for k, e in evidence.items() if k.startswith("db:holding:")]
    flagged = [e["ticker"] for e in scores if e["payload"].get("flags")]
    risky = [e["ticker"] for e in scores if e["payload"].get("risks")]
    movers = [e["ticker"] for e in sorted(scores, key=lambda e: -abs(float(e["payload"].get("ret_1d") or 0)))]
    out = [t.upper() for t in dict.fromkeys(held + flagged + risky + movers) if t and t.upper() in allowed]
    return out[:limit]


def build_analyst_graph(model, tools, effects: dict, prompts: dict, critic=None,
                        budgets: dict | None = None, checkpointer=None, step_models: dict | None = None,
                        critic_fallback=None, profile: dict | None = None):
    """model: plan, gather and draft unless step_models overrides a step ({plan, gather, draft}).
    critic: optional cheaper model for the critique pass; critic_fallback takes over when it errors twice.
    tools: ToolSet (lc_tools, run, baseline, allowed_tickers, caps, calls).
    prompts: {plan, draft, critique, system, ...} text. effects: see module docstring.
    profile: DAILY_PROFILE (default) or another pass definition with the same keys."""
    B = {**BUDGETS, **(budgets or {})}
    M = {"plan": model, "gather": model, "draft": model, **{k: v for k, v in (step_models or {}).items() if v}}
    P = {**DAILY_PROFILE, **(profile or {})}
    PK = P["prompts"]

    def _turn(stats: dict) -> None:
        cb = effects.get("on_turn")
        if cb:
            cb(stats)

    # -- baseline ---------------------------------------------------------
    def baseline(state: AnalystState) -> dict:
        items = tools.baseline(extra=P["baseline_extra"])
        ev = {e["id"]: e for e in items}
        brief = effects["load_brief"](state["as_of"]) or ""
        return {"evidence": ev, "tickers": tools.allowed_tickers(items), "brief": brief,
                "priority": [e["id"] for e in items], "attempt": 0, "started": time.monotonic(),
                "stats": {"turns": 0, "tool_calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
                "status": "running", "messages": []}

    # -- plan -------------------------------------------------------------
    def plan(state: AnalystState) -> dict:
        ev = state["evidence"]
        allowed = set(state["tickers"])
        research = P["research"](state, ev, allowed, B["research_tickers"])
        wp: dict = {"research": research, "claims_to_source": [], "focus": "", "source": "code"}
        if not P["plan_notes"]:
            return {"plan": wp}
        summary = "\n".join(e["excerpt"] for k, e in ev.items()
                            if k.startswith(("db:scores:", "db:holding:", "db:bench:", "db:run:")))
        user = prompts["plan"].format(as_of=state["as_of"], question=state.get("question") or "(daily pass)",
                                      brief=state.get("brief") or "(no brief stored)", summary=summary,
                                      max_tickers=B["research_tickers"], tickers=", ".join(research) or "none")
        try:
            parsed, usage = _structured(M["plan"], PlanNotes, [SystemMessage(prompts["system"]), HumanMessage(user)])
            wp["claims_to_source"] = [c.strip() for c in parsed.claims_to_source if c and c.strip()][:20]
            wp["focus"] = parsed.focus.strip()
        except Exception as e:  # noqa: BLE001 - the ticker list is already built; notes are optional
            log.warning("plan notes failed: %s", e)
            usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": None}
            wp["issue"] = f"plan notes failed ({type(e).__name__}), research list built by code only"
        stats = _add_usage(state["stats"], usage)
        _turn(stats)
        return {"plan": wp, "stats": stats}

    # -- gather -----------------------------------------------------------
    def gather(state: AnalystState) -> dict:
        ev: dict = {}
        stats = dict(state["stats"])
        wp = state["plan"]
        gather_started = time.monotonic()   # the wall budget is gather's own; a slow plan must not eat it
        # Floor: history and levels for every research ticker land before the model says a word.
        for t in wp["research"]:
            for name in PREFETCH:
                try:
                    for it in tools.run(name, {"ticker": t, "days": 30}):
                        ev[it["id"]] = it
                    stats["prefetch"] = stats.get("prefetch", 0) + 1
                except Exception as e:  # noqa: BLE001 - a failed prefetch is just missing evidence
                    log.warning("prefetch %s %s failed: %s", name, t, e)
        bound = M["gather"].bind_tools(tools.lc_tools)
        sys_msg = SystemMessage(prompts["system"] + "\n\n" + prompts[PK["gather"]].format(
            as_of=state["as_of"], tickers=", ".join(wp["research"]) or "none",
            claims="\n".join(f"- {c}" for c in wp.get("claims_to_source", [])) or "- none",
            focus=wp.get("focus", ""), question=state.get("question") or "(daily pass)"))
        index_now = render_index({**state["evidence"], **ev}, state["priority"] + list(ev), budget=60_000)
        messages: list = [sys_msg, HumanMessage(f"Evidence gathered so far:\n{index_now}\n\n"
                                                 "Call the tools you need, then reply with the single word DONE.")]
        template_calls = 0
        status = "running"
        while True:
            if stats["turns"] >= B["turns"]:
                status = "capped"
                break
            if time.monotonic() - gather_started > B["wall_seconds"]:
                status = "capped"
                break
            resp = bound.invoke(messages)
            stats = _add_usage(stats, _usage(resp))
            _turn(stats)
            messages.append(resp)
            calls = getattr(resp, "tool_calls", None) or []
            if not calls:
                break
            for call in calls:
                name, args = call["name"], call.get("args") or {}
                if name == "db_sql" and tools.calls.get("db_sql", 0) >= tools.caps["db_sql"]:
                    content = "budget: db_sql calls exhausted"
                elif tools.is_template(name) and template_calls >= tools.caps["_templates"]:
                    content = "budget: template calls exhausted"
                else:
                    try:
                        items = tools.run(name, args)
                        if tools.is_template(name):
                            template_calls += 1
                        stats["tool_calls"] = stats.get("tool_calls", 0) + 1
                        for it in items:
                            ev[it["id"]] = it
                        content = "\n".join(evidence_block(it) for it in items) or "no rows"
                    except Exception as e:  # noqa: BLE001 - tool errors go back to the model as text
                        content = f"tool error: {type(e).__name__}: {str(e)[:200]}"
                messages.append(ToolMessage(content=content[:20_000], tool_call_id=call["id"]))
        return {"evidence": ev, "messages": messages, "stats": stats, "status": status,
                "capped": status == "capped"}

    # -- ground -----------------------------------------------------------
    def ground(state: AnalystState) -> dict:
        research = set(state["plan"].get("research", []))
        first = [k for k, e in state["evidence"].items() if e.get("ticker") in research]
        return {"priority": list(dict.fromkeys(state["priority"] + first))}

    # -- draft ------------------------------------------------------------
    def draft(state: AnalystState) -> dict:
        index = render_index(state["evidence"], state["priority"])
        tickers = state["plan"].get("research") if P["name"] != "daily" else state["tickers"]
        user = prompts[PK["draft"]].format(as_of=state["as_of"], question=state.get("question") or "(daily pass)",
                                           tickers=", ".join(tickers or []), index=index,
                                           focus=state["plan"].get("focus", ""))
        messages = [SystemMessage(prompts["system"]), HumanMessage(user)]
        if state.get("attempt", 0) > 0 and state.get("report"):
            issues = "\n".join(f"- {i}" for i in state["report"]["issues"][:40])
            messages.append(HumanMessage("Your previous draft lost these claims in review. Cite only ids that exist "
                                         "in the index and keep every level within 15 percent of a cited level:\n"
                                         + issues))
        stats = state["stats"]
        try:
            parsed, usage = _structured(M["draft"], P["schema"], messages)
            d = parsed.model_dump()
            err = None
        except Exception as e:  # noqa: BLE001 - one retry on a parse failure, then invalid
            d, err = None, f"{type(e).__name__}: {str(e)[:300]}"
            usage = {"tokens_in": 0, "tokens_out": 0, "cost_usd": None}
        stats = _add_usage(stats, usage)
        _turn(stats)
        return {"draft": d, "attempt": state.get("attempt", 0) + 1, "stats": stats, "error": err}

    # -- critique ---------------------------------------------------------
    def critique(state: AnalystState) -> dict:
        d = state.get("draft")
        if d is None:
            return {"report": {"claims": 0, "stripped": 0, "ungrounded": 0, "dropped_tickers": [],
                               "issues": [state.get("error") or "no draft"]}, "analysis": None}
        unsupported: list[str] = []
        stats = state["stats"]
        critic_issue = None
        if critic is not None:
            cites = P["cites"](d)
            subset = {k: v for k, v in state["evidence"].items() if k in cites}
            user = prompts["critique"].format(draft=json.dumps(d, default=str),
                                              index=render_index(subset, budget=60_000))
            msgs = [SystemMessage(prompts["system"]), HumanMessage(user)]
            last_err: Exception | None = None
            # Primary twice (upstream 429s are short), then the fallback critic, then give up loudly.
            for attempt, m in enumerate((critic, critic, critic_fallback)):
                if m is None:
                    continue
                try:
                    parsed, usage = _structured(m, Critique, msgs)
                    unsupported = [i.text for i in parsed.unsupported]
                    stats = _add_usage(stats, usage)
                    _turn(stats)
                    if m is critic_fallback:
                        critic_issue = f"critic fell back after {type(last_err).__name__}"
                    last_err = None
                    break
                except Exception as e:  # noqa: BLE001 - critic is advisory; code checks still run
                    last_err = e
                    log.warning("critic attempt %d failed: %s", attempt + 1, e)
                    if attempt == 0 and B["critic_retry_sleep"]:
                        time.sleep(B["critic_retry_sleep"])
            if last_err is not None:
                critic_issue = f"critic unavailable ({type(last_err).__name__}), facts not checked"
        clean, report = P["validate"](d, state["evidence"], state["tickers"], unsupported)
        if critic_issue:
            report["issues"] = list(report.get("issues", [])) + [critic_issue]
        ratio = (report["stripped"] / report["claims"]) if report["claims"] else 1.0
        report["stripped_ratio"] = round(ratio, 3)
        ok = ratio <= B["max_stripped_ratio"]
        analysis = None
        if ok or state["attempt"] >= 2:
            clean["disclaimer"] = DISCLAIMER
            analysis = clean
        return {"report": report, "analysis": analysis, "stats": stats,
                "status": ("ok" if ok else "invalid") if analysis is not None else state["status"]}

    def after_critique(state: AnalystState) -> str:
        # One retry: a parse failure or too many stripped claims sends the draft back once.
        if state.get("analysis") is None and state.get("attempt", 0) < 2:
            return "draft"
        return "hitl"

    # -- hitl -------------------------------------------------------------
    def hitl(state: AnalystState) -> dict:
        if not effects.get("hitl_enabled", lambda: False)():
            return {"decision": {"action": "approve"}}
        decision = interrupt({"as_of": state["as_of"], "analysis": state.get("analysis"),
                              "report": state.get("report"), "stats": state.get("stats")})
        return {"decision": decision if isinstance(decision, dict) else {"action": "approve"}}

    # -- persist ----------------------------------------------------------
    def persist(state: AnalystState) -> dict:
        a = state.get("analysis")
        decision = state.get("decision") or {"action": "approve"}
        status = state.get("status") or "error"
        if a is None:
            status = "invalid" if state.get("draft") is not None or state.get("attempt", 0) >= 2 else "error"
        elif decision.get("action") == "discard":
            status = "discarded"
        elif status == "running":
            status = "ok"
        report = dict(state.get("report") or {})
        if state.get("capped"):
            report["issues"] = list(report.get("issues", [])) + ["gather stopped at budget"]
        if (state.get("plan") or {}).get("issue"):
            report["issues"] = list(report.get("issues", [])) + [state["plan"]["issue"]]
        if status == "capped" and a is not None:
            status = "ok"
        stats = dict(state["stats"])
        stats["wall_seconds"] = round(time.monotonic() - state["started"], 1)
        payload = {"as_of": state["as_of"], "mode": state.get("mode", "daily"), "status": status,
                   "analysis": a, "markdown": P["markdown"](a) if a else None,
                   "report": report, "stats": stats, "error": state.get("error"),
                   "evidence": state["evidence"], "plan": state.get("plan"), "note": decision.get("note")}
        analysis_id = effects["persist"](payload)
        return {"status": status, "stats": {**stats, "analysis_id": analysis_id}}

    g = StateGraph(AnalystState)
    for name, fn in (("baseline", baseline), ("plan", plan), ("gather", gather), ("ground", ground),
                     ("draft", draft), ("critique", critique), ("hitl", hitl), ("persist", persist)):
        g.add_node(name, fn)
    g.add_edge(START, "baseline")
    g.add_edge("baseline", "plan")
    g.add_edge("plan", "gather")
    g.add_edge("gather", "ground")
    g.add_edge("ground", "draft")
    g.add_edge("draft", "critique")
    g.add_conditional_edges("critique", after_critique, {"draft": "draft", "hitl": "hitl"})
    g.add_edge("hitl", "persist")
    g.add_edge("persist", END)
    return g.compile(checkpointer=checkpointer)
