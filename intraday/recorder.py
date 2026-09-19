"""Recorder loop: read-only IEX 1-min bars + quote snapshots -> intraday.*, under a
single-writer lease, with point-in-time timestamps. No orders. REST-poll skeleton;
websocket streaming is the next iteration."""
import time
import uuid
from datetime import datetime, timedelta, timezone

from psycopg2.extras import Json

from . import alpaca_data, clock, config, db


def _session_id(trade_date):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"intraday:{trade_date}"))


def _parse(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _bar_rows(sid, bars_by_sym, received):
    rows, max_end = [], None
    for sym, arr in bars_by_sym.items():
        for b in arr:
            end = _parse(b["t"]) + timedelta(minutes=1)  # bar 't' is the minute start; store the completion
            rows.append(dict(session_id=sid, symbol=sym, bar_end=end,
                             open=b.get("o"), high=b.get("h"), low=b.get("l"), close=b.get("c"),
                             volume=b.get("v"), vwap=b.get("vw"), trade_count=b.get("n"),
                             feed=config.FEED, available_at=received, received_at=received,
                             payload_hash=db.payload_hash(b)))
            if max_end is None or end > max_end:
                max_end = end
    return rows, max_end


def _quote_rows(sid, quotes, received):
    rows = []
    for sym, q in quotes.items():
        bid, ask = q.get("bp"), q.get("ap")
        mid = ((bid or 0) + (ask or 0)) / 2
        spread_bps = round((ask - bid) / mid * 10000, 3) if bid and ask and mid > 0 else None
        payload = {"bid": bid, "ask": ask, "bid_sz": q.get("bs"), "ask_sz": q.get("as"), "spread_bps": spread_bps}
        rows.append(dict(session_id=sid, source=config.SOURCE, symbol=sym,
                         event_at=_parse(q["t"]) if q.get("t") else received,
                         available_at=received, received_at=received, feed=config.FEED,
                         payload_hash=db.payload_hash({**payload, "t": q.get("t")}), payload=Json(payload)))
    return rows


def run(once=False, warmup_minutes=30):
    conn = db.connect()
    owner, token = str(uuid.uuid4()), int(time.time() * 1000)
    td = clock.trade_date()
    sid = _session_id(td)
    if not db.claim_lease(conn, sid, td, owner, token):
        raise SystemExit("another recorder holds this session's lease; refusing to start (single writer)")
    is_open, _, _ = clock.market_state()
    print(f"recorder up: session={sid} feed={config.FEED} symbols={len(config.SYMBOLS)} "
          f"market_open={is_open} (paper clock, read-only market data, NO orders)")

    start = (datetime.now(timezone.utc) - timedelta(minutes=warmup_minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")
    last_end = None
    try:
        while True:
            t0 = time.time()
            if not db.renew_lease(conn, sid, td, owner, token):
                raise SystemExit("lost lease (fenced by a newer writer); exiting")
            provider_age, http_fail = None, 0
            try:
                now = datetime.now(timezone.utc)
                bars = alpaca_data.bars_1min(config.SYMBOLS, start)
                rows, max_end = _bar_rows(sid, bars, now)
                nb = db.write_bars(conn, rows)
                if max_end:
                    last_end = max_end
                    start = (max_end - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")  # overlap for corrections
                    provider_age = (datetime.now(timezone.utc) - max_end).total_seconds()
                nq = db.write_quotes(conn, _quote_rows(sid, alpaca_data.latest_quotes(config.SYMBOLS),
                                                       datetime.now(timezone.utc)))
                missing = [s for s in config.SYMBOLS if s not in bars]
                status = "ok" if not missing else "degraded"
            except Exception as e:  # a dead feed degrades the recorder; it never crashes the loop
                nb, nq, http_fail, missing, status = 0, 0, 1, list(config.SYMBOLS), "outage"
                print("poll error:", str(e)[:200])
            dur = int((time.time() - t0) * 1000)
            db.heartbeat(conn, provider_age, dur, missing, http_fail, status)
            print(f"poll: bars+{nb} quotes+{nq} last_bar_end={last_end} "
                  f"provider_age_s={provider_age} status={status} {dur}ms")
            if once:
                break
            time.sleep(config.POLL_SECONDS)
    finally:
        conn.close()
