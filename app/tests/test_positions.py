"""Positions endpoints. Needs a live app on a throwaway DB.

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
TICKER = "NVDA"
OTHER = "INTC"


def call(method: str, path: str, **kw) -> requests.Response:
    return requests.request(method, URL + path, headers=H, timeout=20, **kw)


@pytest.fixture(autouse=True)
def _clean():
    call("DELETE", f"/api/positions/{TICKER}")
    call("DELETE", f"/api/positions/{OTHER}")
    yield
    call("DELETE", f"/api/positions/{TICKER}")
    call("DELETE", f"/api/positions/{OTHER}")


def test_put_creates_position_and_derives_holding_tag():
    r = call("PUT", f"/api/positions/{TICKER}",
             json={"qty": 0.455, "avg_cost": 200.0, "opened_at": "2026-06-01"})
    assert r.status_code == 200, r.text
    pos = r.json()["position"]
    assert pos["qty"] == pytest.approx(0.455)

    row = next(w for w in call("GET", "/api/watchlist").json()["watchlist"] if w["ticker"] == TICKER)
    assert "holding" in row["tags"]
    assert row["qty"] == pytest.approx(0.455)
    assert row["avg_cost"] == pytest.approx(200.0)


def test_valuation_math_and_total():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 0.455, "avg_cost": 200.0})
    d = call("GET", "/api/positions").json()
    h = next(p for p in d["positions"] if p["ticker"] == TICKER)

    assert h["cost_basis"] == pytest.approx(0.455 * 200.0)
    assert h["market_value"] == pytest.approx(0.455 * h["last_close"])
    assert h["gain"] == pytest.approx(0.455 * (h["last_close"] - 200.0))
    assert h["gain_pct"] == pytest.approx((h["last_close"] - 200.0) / 200.0)
    assert h["weight"] == pytest.approx(1.0)  # sole position

    assert d["total"]["market_value"] == pytest.approx(h["market_value"])
    assert d["total"]["count"] == 1


def test_weight_is_share_of_equity():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 1, "avg_cost": 100.0})
    call("PUT", f"/api/positions/{OTHER}", json={"qty": 1, "avg_cost": 10.0})
    d = call("GET", "/api/positions").json()
    assert sum(p["weight"] for p in d["positions"]) == pytest.approx(1.0)


def test_retire_with_open_position_is_refused():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 0.455, "avg_cost": 200.0})
    r = call("PATCH", f"/api/watchlist/{TICKER}", json={"active": False})
    assert r.status_code == 409
    assert "close the position" in r.json()["detail"].lower()


def test_close_drops_the_tag_and_leaves_a_zero_history_row():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 0.455, "avg_cost": 200.0})
    assert call("DELETE", f"/api/positions/{TICKER}").status_code == 200

    row = next(w for w in call("GET", "/api/watchlist").json()["watchlist"] if w["ticker"] == TICKER)
    assert "holding" not in row["tags"]
    assert row["qty"] is None

    hist = call("GET", f"/api/positions/{TICKER}/history").json()["history"]
    assert hist[0]["qty"] == 0
    assert hist[0]["changed_by"] == "ui"


def test_history_records_every_change():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 1.0, "avg_cost": 100.0})
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 0.5, "avg_cost": 100.0})
    qtys = [h["qty"] for h in call("GET", f"/api/positions/{TICKER}/history").json()["history"]]
    assert qtys[:2] == [0.5, 1.0]


def test_holding_tag_cannot_be_set_by_hand():
    r = call("PATCH", f"/api/watchlist/{OTHER}", json={"tags": ["tech", "holding"]})
    assert r.status_code == 200
    assert "holding" not in r.json()["ticker"]["tags"]


def test_holding_tag_survives_a_tag_edit_while_held():
    call("PUT", f"/api/positions/{TICKER}", json={"qty": 1.0, "avg_cost": 100.0})
    r = call("PATCH", f"/api/watchlist/{TICKER}", json={"tags": ["ai"]})
    assert set(r.json()["ticker"]["tags"]) == {"ai", "holding"}


def test_qty_must_be_positive():
    assert call("PUT", f"/api/positions/{TICKER}", json={"qty": 0}).status_code == 422
    assert call("PUT", f"/api/positions/{TICKER}", json={"qty": -1}).status_code == 422


def test_close_without_a_position_is_404():
    assert call("DELETE", f"/api/positions/{TICKER}").status_code == 404


def test_positions_routes_require_auth():
    for method, path in [("GET", "/api/positions"), ("PUT", f"/api/positions/{TICKER}"),
                         ("DELETE", f"/api/positions/{TICKER}"),
                         ("GET", f"/api/positions/{TICKER}/history")]:
        assert requests.request(method, URL + path, timeout=20, json={"qty": 1}).status_code == 401
        r = requests.request(method, URL + path, headers={"Authorization": "Bearer wrong"},
                             timeout=20, json={"qty": 1})
        assert r.status_code == 403
