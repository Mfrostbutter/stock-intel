"""Entry watch rule layer. Pure functions over one row; no DB, no model.

rule_signal(row) turns stored indicators plus the user's zone into a deterministic
signal the screen can show at once and the analyst must cite.
"""

from __future__ import annotations

from typing import Any

SIGNALS = ("enter", "near", "wait", "avoid")
NEAR_PCT = 0.03   # within this fraction of the zone counts as near


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def trend_of(price: float | None, sma50: float | None, sma200: float | None) -> str:
    """up: above both averages; down: below both; mixed otherwise; unknown when data is missing."""
    if price is None or (sma50 is None and sma200 is None):
        return "unknown"
    above = [price > s for s in (sma50, sma200) if s is not None]
    if all(above):
        return "up"
    if not any(above):
        return "down"
    return "mixed"


def rsi_band(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi < 30:
        return "oversold"
    if rsi < 45:
        return "low"
    if rsi <= 65:
        return "neutral"
    if rsi <= 70:
        return "high"
    return "overbought"


def zone_distance(price: float | None, low: float | None, high: float | None) -> tuple[float | None, bool]:
    """(signed distance from the zone as a fraction of price, in_zone). Positive is above the zone."""
    if price is None or price <= 0 or (low is None and high is None):
        return None, False
    lo = low if low is not None else high
    hi = high if high is not None else low
    if lo <= price <= hi:
        return 0.0, True
    if price > hi:
        return (price - hi) / price, False
    return (price - lo) / price, False


def rule_signal(row: dict, near_pct: float = NEAR_PCT) -> dict:
    """Deterministic entry read for one ticker.

    row keys used: close (or price), zone_low, zone_high, invalidation, sma50, sma200, rsi14,
    vol_z20, news_score, flags, risks, kind, avg_cost. Missing keys degrade to unknown, never raise.
    kind = add (a holding): price at or above avg_cost can never be enter or near, adding would not
    lower the average.
    """
    price = _f(row.get("price")) or _f(row.get("close"))
    low, high = _f(row.get("zone_low")), _f(row.get("zone_high"))
    inval = _f(row.get("invalidation"))
    sma50, sma200, rsi = _f(row.get("sma50")), _f(row.get("sma200")), _f(row.get("rsi14"))
    vol_z, sent = _f(row.get("vol_z20")), _f(row.get("news_score"))
    flags = list(row.get("flags") or [])
    risks = list(row.get("risks") or [])
    kind = row.get("kind") or "new"
    avg_cost = _f(row.get("avg_cost")) if kind == "add" else None
    vs_cost = ((price - avg_cost) / avg_cost) if (price is not None and avg_cost) else None

    dist, in_zone = zone_distance(price, low, high)
    trend = trend_of(price, sma50, sma200)
    band = rsi_band(rsi)
    below_inval = inval is not None and price is not None and price < inval
    reasons: list[str] = []

    if price is None:
        signal = "wait"
        reasons.append("no stored price")
    elif below_inval:
        signal = "avoid"
        reasons.append(f"close {price:.2f} is below the invalidation {inval:.2f}")
    elif vs_cost is not None and vs_cost >= 0:
        signal = "wait"
        reasons.append(f"{vs_cost * 100:.1f}% above the average cost, adding would raise it")
    elif risks:
        signal = "avoid" if in_zone else "wait"
        reasons.append("risk flag " + ", ".join(risks))
    elif in_zone:
        signal = "enter"
        reasons.append("close inside the zone")
    elif dist is not None and abs(dist) <= near_pct:
        signal = "near"
        reasons.append(f"{abs(dist) * 100:.1f}% {'above' if dist > 0 else 'below'} the zone")
    elif dist is None and flags:
        signal = "near"
        reasons.append("no zone set; entry flag " + ", ".join(flags))
    else:
        signal = "wait"
        if dist is not None:
            reasons.append(f"{abs(dist) * 100:.1f}% {'above' if dist > 0 else 'below'} the zone")
        else:
            reasons.append("no zone set and no entry flag")

    if vs_cost is not None and vs_cost < 0:
        reasons.append(f"{-vs_cost * 100:.1f}% below the average cost")
    if trend != "unknown":
        reasons.append(f"trend {trend}")
    if band != "unknown":
        reasons.append(f"rsi {band}")
    if flags and signal != "near":
        reasons.append("flag " + ", ".join(flags))
    if sent is not None and sent < -0.15:
        reasons.append("negative 7d news")
    if vol_z is not None and vol_z > 2:
        reasons.append("volume spike")

    return {
        "signal": signal,
        "kind": kind,
        "price": price,
        "vs_cost_pct": None if vs_cost is None else round(vs_cost, 4),
        "distance_pct": None if dist is None else round(dist, 4),
        "in_zone": in_zone,
        "below_invalidation": below_inval,
        "trend": trend,
        "rsi_band": band,
        "flags": flags,
        "risks": risks,
        "reasons": reasons,
    }


def add_zone_cap(avg_cost: float | None, discount: float = 0.02) -> float | None:
    """Highest zone top that still lowers the average: the cost basis less the discount."""
    avg = _f(avg_cost)
    if avg is None or avg <= 0:
        return None
    return round(avg * (1 - discount), 4)


def signal_rank(sig: str) -> int:
    """Sort key: enter first, then near, wait, avoid."""
    return SIGNALS.index(sig) if sig in SIGNALS else len(SIGNALS)
