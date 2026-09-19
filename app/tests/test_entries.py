"""Entry watch endpoints. Needs a live app on a throwaway DB with sql/008 applied.

Set STOCK_INTEL_TEST_URL and STOCK_INTEL_TEST_TOKEN; see tests/README.md.
"""

from __future__ import annotations

import os

import pytest
import requests

URL = os.environ.get("STOCK_INTEL_TEST_URL", "").rstrip("/")
TOKEN = os.environ.get("STOCK_INTEL_TEST_TOKEN", "")

pytestmark = pytest.mark.skipif(
    not (URL and TOKEN), reason="STOCK_INTEL_TEST_URL / STOCK_INTEL_TEST_TOKEN not set"
)

H = {"Authorization": f"Bearer {TOKEN}"}
TICKER = "INTC"   # seeded on the watchlist, so no Finnhub call is needed


def call(method: str, path: str, **kw) -> requests.Response:
    return requests.request(method, URL + path, headers=H, timeout=20, **kw)


def row(sym: str) -> dict | None:
    return next((e for e in call("GET", "/api/entries?include_dropped=true").json()["entries"] if e["ticker"] == sym), None)


@pytest.fixture(autouse=True)
def _clean():
    call("DELETE", f"/api/entries/{TICKER}")
    yield
    call("DELETE", f"/api/entries/{TICKER}")


def test_add_returns_rule_signal_and_counts():
    r = call("POST", "/api/entries", json={"ticker": TICKER, "zone_low": 80, "zone_high": 90,
                                           "invalidation": 70, "horizon": "weeks", "thesis": "test"})
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["entry"]["status"] == "watching"
    e = next(x for x in d["entries"] if x["ticker"] == TICKER)
    assert e["rule"]["signal"] in ("enter", "near", "wait", "avoid")
    assert d["counts"]["watching"] >= 1


def test_zone_order_is_checked():
    r = call("POST", "/api/entries", json={"ticker": TICKER, "zone_low": 90, "zone_high": 80})
    assert r.status_code == 422


def test_patch_status_and_history():
    call("POST", "/api/entries", json={"ticker": TICKER, "zone_low": 80, "zone_high": 90})
    r = call("PATCH", f"/api/entries/{TICKER}", json={"status": "entered", "notes": "filled"})
    assert r.status_code == 200, r.text
    assert row(TICKER)["status"] == "entered"
    h = call("GET", f"/api/entries/{TICKER}/signals").json()
    assert [x["status"] for x in h["history"]][:2] == ["entered", "watching"]


def test_drop_archives_and_readd_revives():
    call("POST", "/api/entries", json={"ticker": TICKER})
    assert call("DELETE", f"/api/entries/{TICKER}").status_code == 200
    assert row(TICKER)["status"] == "dropped"
    assert TICKER not in [e["ticker"] for e in call("GET", "/api/entries").json()["entries"]]
    call("POST", "/api/entries", json={"ticker": TICKER, "zone_low": 1, "zone_high": 2})
    assert row(TICKER)["status"] == "watching"


def test_signal_endpoint_refuses_when_nothing_live():
    r = call("POST", "/api/entries/signal", json={"tickers": ["ZZZZ"]})
    assert r.status_code == 404
