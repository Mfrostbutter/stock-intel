"""Entry-signal profile for the analyst graph: research set, draft schema, checks, rendering.

Pure functions over dicts. The graph takes this as `profile`; service.py wires the DB side.
"""

from __future__ import annotations

from .evidence import _check_claim, grounded, levels_for, strip_invalidation_prefix
from .schemas import EntrySignalDraft


def research_entries(state: dict, evidence: dict, allowed: set[str], limit: int) -> list[str]:
    """The tickers under review are given up front (state.focus_tickers), never picked by rule."""
    want = [t.upper() for t in state.get("focus_tickers") or []]
    return [t for t in dict.fromkeys(want) if t in allowed][:limit]


def cite_ids_signals(a: dict) -> set[str]:
    out: set[str] = set()
    for s in a.get("signals", []):
        for c in s.get("rationale", []) + [s.get("invalidation") or {}]:
            out.update(c.get("cites") or [])
    return out


def validate_signals(draft: dict, evidence: dict, allowed: list[str],
                     critic_unsupported: list[str] | None = None) -> tuple[dict, dict]:
    """Strip unsupported claims; drop a ticker whose rationale is empty after stripping.

    Returns (clean_draft, report). Report shape matches validate_draft's.
    """
    allowed_set = set(allowed)
    unsupported = {t.strip() for t in (critic_unsupported or []) if t and t.strip()}
    issues: list[str] = []
    claims = stripped = ungrounded = 0
    dropped: list[str] = []

    def take(claim: dict | None, ticker: str, where: str) -> dict | None:
        nonlocal claims, stripped, ungrounded
        if not claim:
            return None
        claims += 1
        if claim.get("text", "").strip() in unsupported:
            issues.append(f"{where}: critic marked unsupported, stripped: {claim['text'][:120]}")
            stripped += 1
            return None
        c = _check_claim(claim, evidence, ticker, issues, where)
        if c is None:
            stripped += 1
        elif c.get("ungrounded"):
            ungrounded += 1
        return c

    clean: dict = {"signals": [], "data_gaps": list(draft.get("data_gaps", []))}
    for s in draft.get("signals", []):
        t = (s.get("ticker") or "").upper()
        if t not in allowed_set:
            dropped.append(t)
            continue
        rat = [c for c in (take(cl, t, f"signals.{t}.rationale") for cl in s.get("rationale", [])) if c]
        inv = take(strip_invalidation_prefix(s.get("invalidation")), t, f"signals.{t}.invalidation")
        if not rat:
            issues.append(f"signals.{t}: no supported rationale, dropped")
            dropped.append(t)
            continue
        out = dict(s, ticker=t, rationale=rat,
                   invalidation=inv or {"text": "", "cites": [], "levels": [], "stripped": True})
        zone = s.get("suggested_zone")
        lv = levels_for(evidence, t, [c for cl in rat for c in cl["cites"]])
        if zone:
            bad = [v for v in (zone.get("low"), zone.get("high")) if v is not None and not grounded(float(v), lv)]
            if bad:
                ungrounded += 1
                issues.append(f"signals.{t}.suggested_zone: ungrounded {bad}")
                out["suggested_zone"] = dict(zone, ungrounded=True)
        si = s.get("suggested_invalidation")
        if si is not None and not grounded(float(si), lv):
            ungrounded += 1
            issues.append(f"signals.{t}.suggested_invalidation: ungrounded {si}")
            out["suggested_invalidation"] = None
        clean["signals"].append(out)

    if dropped:
        issues.append(f"tickers dropped: {sorted(set(dropped))}")
    for iss in issues:
        if "stripped" in iss or "dropped" in iss:
            clean["data_gaps"].append(iss)
    report = {"claims": claims, "stripped": stripped, "ungrounded": ungrounded,
              "dropped_tickers": sorted(set(dropped)), "issues": issues}
    return clean, report


def proposed_zone(signal: dict, entry: dict, cap: float | None = None) -> dict | None:
    """The zone to write onto a row that has none: the model's grounded suggestion, else nothing.

    entry is the db:entry payload the model saw. Never overrides a zone the user typed.
    cap (add rows): the highest allowed zone top, the cost basis less a discount. A top above it is
    clipped; a zone entirely above it is refused, adding there would not lower the average.
    """
    if entry.get("zone_low") is not None or entry.get("zone_high") is not None:
        return None
    z = signal.get("suggested_zone") or {}
    if z.get("ungrounded") or z.get("low") is None or z.get("high") is None:
        return None
    low, high = float(z["low"]), float(z["high"])
    if low <= 0 or low > high:
        return None
    clipped = False
    if cap is not None:
        if low >= cap:
            return None
        if high > cap:
            high, clipped = cap, True
    inv = signal.get("suggested_invalidation")
    inv = float(inv) if inv is not None and float(inv) < low else None
    return {"zone_low": low, "zone_high": high, "invalidation": inv, "clipped": clipped}


def signals_markdown(a: dict) -> str:
    def line(c: dict) -> str:
        cites = " ".join(f"[{x}]" for x in c.get("cites", []))
        flag = " (ungrounded)" if c.get("ungrounded") else ""
        return f"{c.get('text', '')}{flag} {cites}".strip()

    out = ["## Entry signals"]
    for s in a.get("signals", []):
        out.append(f"**{s['ticker']}**: {s['signal']} (confidence {s.get('confidence', 0):.2f}, zone {s.get('zone_check', '?')})")
        if s.get("summary"):
            out.append(f"- {s['summary']}")
        out += [f"- {line(c)}" for c in s.get("rationale", [])]
        if s.get("invalidation", {}).get("text"):
            out.append(f"- Invalidation: {line(s['invalidation'])}")
        for w in s.get("watch_for", []):
            out.append(f"- Watch: {w}")
    if a.get("data_gaps"):
        out.append("\n## Data gaps")
        out += [f"- {g}" for g in a["data_gaps"]]
    if a.get("disclaimer"):
        out.append(f"\n_{a['disclaimer']}_")
    return "\n".join(out)


ENTRY_PROFILE = {
    "name": "entry",
    "research": research_entries,
    "plan_notes": False,               # focus is fixed; no plan model call
    "baseline_extra": ("entry_watch",),
    "schema": EntrySignalDraft,
    "validate": validate_signals,
    "cites": cite_ids_signals,
    "markdown": signals_markdown,
    "prompts": {"gather": "entry_gather", "draft": "entry_draft"},
}
