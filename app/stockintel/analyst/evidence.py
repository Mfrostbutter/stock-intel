"""Evidence index and the deterministic checks the critique step runs over a draft.

Ticker allow-list, citation validity, number grounding (15 percent of a cited level).
Pure functions over dicts; no model, no IO.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .tools import LEVEL_KEYS, jsonable

LEVEL_TOLERANCE = 0.15
MAX_INDEX_CHARS = 90_000


def evidence_block(item: dict, full: bool = True) -> str:
    """One <evidence> element. Payload is JSON; the model is told this content is untrusted data."""
    body = json.dumps(jsonable(item["payload"]), default=str, separators=(",", ":")) if full else item["excerpt"]
    return f'<evidence id="{item["id"]}" kind="{item["kind"]}" trust="untrusted">{body}</evidence>'


def render_index(evidence: dict[str, dict], priority: list[str] | None = None, budget: int = MAX_INDEX_CHARS) -> str:
    """Every evidence item, full payload while budget lasts, excerpt only after. Priority ids first."""
    order = list(priority or []) + [k for k in evidence if k not in set(priority or [])]
    parts, used = [], 0
    for k in order:
        item = evidence.get(k)
        if not item:
            continue
        block = evidence_block(item, full=True)
        if used + len(block) > budget:
            block = evidence_block(item, full=False)
        parts.append(block)
        used += len(block)
    return "\n".join(parts)


def _walk_levels(obj: Any, out: set[float], key: str | None = None) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_levels(v, out, k)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_levels(v, out, key)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        if key in LEVEL_KEYS and obj > 0:
            out.add(float(obj))


def levels_for(evidence: dict[str, dict], ticker: str | None, cites: list[str]) -> set[float]:
    """Price levels the draft may use for a ticker: from the cited items, plus any item tagged with the ticker."""
    out: set[float] = set()
    for cid in cites:
        item = evidence.get(cid)
        if item:
            _walk_levels(item["payload"], out)
    if ticker:
        for item in evidence.values():
            if item.get("ticker") == ticker:
                _walk_levels(item["payload"], out)
    return out


def grounded(value: float, levels: set[float], tol: float = LEVEL_TOLERANCE) -> bool:
    return any(abs(value - lv) <= tol * lv for lv in levels if lv > 0)


def _check_claim(claim: dict, evidence: dict, ticker: str | None, issues: list[str], where: str) -> dict | None:
    """Return the claim with invalid cites removed, or None if nothing valid supports it."""
    cites = [c for c in claim.get("cites", []) if c in evidence]
    bad = [c for c in claim.get("cites", []) if c not in evidence]
    if bad:
        issues.append(f"{where}: unknown citation {', '.join(bad)}")
    if not cites:
        issues.append(f"{where}: no valid citation, stripped: {claim.get('text', '')[:120]}")
        return None
    lv = levels_for(evidence, ticker, cites)
    ungrounded = [x for x in claim.get("levels", []) if not grounded(float(x), lv)]
    out = dict(claim, cites=cites)
    if ungrounded:
        issues.append(f"{where}: ungrounded level(s) {ungrounded}")
        out["ungrounded"] = ungrounded
    return out


_INV_PREFIX = re.compile(r"^\s*invalidation\s*(?::|-|is|would be|=)?\s*", re.IGNORECASE)


def strip_invalidation_prefix(claim: dict | None) -> dict | None:
    """The renderer labels the line; drop a duplicate 'Invalidation:' / 'Invalidation is' the model wrote."""
    if not claim or not claim.get("text"):
        return claim
    text = _INV_PREFIX.sub("", claim["text"], count=1).strip()
    if text and text != claim["text"]:
        text = text[0].upper() + text[1:]
        return dict(claim, text=text)
    return claim


def validate_draft(draft: dict, evidence: dict[str, dict], allowed: list[str],
                   critic_unsupported: list[str] | None = None) -> tuple[dict, dict]:
    """Strip what the evidence does not support. Returns (clean_draft, report).

    report: {claims, stripped, dropped_tickers, issues, ungrounded}
    """
    allowed_set = set(allowed)
    unsupported = {t.strip() for t in (critic_unsupported or []) if t and t.strip()}
    issues: list[str] = []
    claims = stripped = ungrounded = 0
    dropped: list[str] = []

    def take(claim: dict | None, ticker: str | None, where: str) -> dict | None:
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

    clean: dict = {"market_read": [], "holdings": [], "entries": [], "avoid": [],
                   "questions": list(draft.get("questions", [])), "data_gaps": list(draft.get("data_gaps", []))}

    for i, cl in enumerate(draft.get("market_read", [])):
        c = take(cl, None, f"market_read[{i}]")
        if c:
            clean["market_read"].append(c)

    for h in draft.get("holdings", []):
        t = (h.get("ticker") or "").upper()
        if t not in allowed_set:
            dropped.append(t)
            continue
        rat = [c for c in (take(cl, t, f"holdings.{t}.rationale") for cl in h.get("rationale", [])) if c]
        inv = take(strip_invalidation_prefix(h.get("invalidation")), t, f"holdings.{t}.invalidation")
        if not rat:
            issues.append(f"holdings.{t}: no supported rationale, dropped")
            continue
        clean["holdings"].append(dict(h, ticker=t, rationale=rat,
                                      invalidation=inv or {"text": "", "cites": [], "levels": [], "stripped": True}))

    for e in draft.get("entries", []):
        t = (e.get("ticker") or "").upper()
        if t not in allowed_set:
            dropped.append(t)
            continue
        rat = [c for c in (take(cl, t, f"entries.{t}.rationale") for cl in e.get("rationale", [])) if c]
        inv = take(strip_invalidation_prefix(e.get("invalidation")), t, f"entries.{t}.invalidation")
        if not rat:
            issues.append(f"entries.{t}: no supported rationale, dropped")
            continue
        entry = dict(e, ticker=t, rationale=rat,
                     invalidation=inv or {"text": "", "cites": [], "levels": [], "stripped": True})
        zone = e.get("entry_zone") or {}
        lv = levels_for(evidence, t, [c for cl in rat for c in cl["cites"]])
        zone_bad = [v for v in (zone.get("low"), zone.get("high")) if v is not None and not grounded(float(v), lv)]
        if zone_bad:
            ungrounded += 1
            issues.append(f"entries.{t}.entry_zone: ungrounded {zone_bad}")
            entry["entry_zone"] = dict(zone, ungrounded=True)
        clean["entries"].append(entry)

    for a in draft.get("avoid", []):
        t = (a.get("ticker") or "").upper()
        if t not in allowed_set:
            dropped.append(t)
            continue
        r = take(a.get("reason"), t, f"avoid.{t}")
        if r:
            clean["avoid"].append(dict(a, ticker=t, reason=r))

    if dropped:
        issues.append(f"tickers outside the watchlist dropped: {sorted(set(dropped))}")
    for iss in issues:
        if "stripped" in iss or "dropped" in iss:
            clean["data_gaps"].append(iss)

    report = {"claims": claims, "stripped": stripped, "ungrounded": ungrounded,
              "dropped_tickers": sorted(set(dropped)), "issues": issues}
    return clean, report


def cite_ids_draft(a: dict) -> set[str]:
    """Every evidence id an AnalysisDraft cites."""
    out: set[str] = set()

    def take(c):
        if isinstance(c, dict):
            out.update(c.get("cites") or [])

    for c in a.get("market_read", []):
        take(c)
    for h in a.get("holdings", []) + a.get("entries", []):
        for c in h.get("rationale", []):
            take(c)
        take(h.get("invalidation"))
    for x in a.get("avoid", []):
        take(x.get("reason"))
    return out


def to_markdown(a: dict) -> str:
    """Compact markdown rendering for the panel and for the fallback view."""
    def line(c: dict) -> str:
        cites = " ".join(f"[{x}]" for x in c.get("cites", []))
        flag = " (ungrounded)" if c.get("ungrounded") else ""
        return f"{c.get('text', '')}{flag} {cites}".strip()

    out = ["## Market read", *[f"- {line(c)}" for c in a.get("market_read", [])]]
    if a.get("holdings"):
        out.append("\n## Holdings")
        for h in a["holdings"]:
            out.append(f"**{h['ticker']}**: {h['stance']} (confidence {h.get('confidence', 0):.2f})")
            out += [f"- {line(c)}" for c in h.get("rationale", [])]
            if h.get("invalidation", {}).get("text"):
                out.append(f"- Invalidation: {line(h['invalidation'])}")
    if a.get("entries"):
        out.append("\n## Entries")
        for e in a["entries"]:
            z = e.get("entry_zone", {})
            out.append(f"**{e['ticker']}**: {e.get('setup', '')}, zone {z.get('low')} to {z.get('high')}"
                       f"{' (ungrounded)' if z.get('ungrounded') else ''}, {e.get('horizon', '')}, "
                       f"confidence {e.get('confidence', 0):.2f}")
            out += [f"- {line(c)}" for c in e.get("rationale", [])]
            if e.get("invalidation", {}).get("text"):
                out.append(f"- Invalidation: {line(e['invalidation'])}")
    if a.get("avoid"):
        out.append("\n## Avoid")
        out += [f"- **{x['ticker']}**: {line(x['reason'])}" for x in a["avoid"]]
    if a.get("questions"):
        out.append("\n## Open questions")
        out += [f"- {q}" for q in a["questions"]]
    if a.get("data_gaps"):
        out.append("\n## Data gaps")
        out += [f"- {g}" for g in a["data_gaps"]]
    if a.get("disclaimer"):
        out.append(f"\n_{a['disclaimer']}_")
    return "\n".join(out)
