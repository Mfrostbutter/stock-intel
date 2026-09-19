"""Postgres access for the stocks DB. One pool, dict rows, search_path pinned to intel."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg_pool import ConnectionPool

from .config import PG_DB, PG_HOST, PG_PASSWORD, PG_PORT, PG_USER

log = logging.getLogger("stockintel.db")

_CONNINFO = (
    f"host={PG_HOST} port={PG_PORT} dbname={PG_DB} user={PG_USER} "
    f"password={PG_PASSWORD} connect_timeout=10 client_encoding=UTF8 "
    f"options=-csearch_path=intel,public"
)


def _configure(conn: psycopg.Connection) -> None:
    # numeric -> float, not Decimal. Decimal serialises as a JSON string, which breaks
    # numeric sorting and arithmetic in the SPA. This is a display API, not a ledger.
    conn.adapters.register_loader("numeric", FloatLoader)


# open=False so an unreachable DB does not crash import; the first query opens it.
pool = ConnectionPool(
    _CONNINFO,
    min_size=1,
    max_size=4,
    open=False,
    configure=_configure,
    kwargs={"row_factory": dict_row},
)


def open_pool() -> None:
    pool.open()


def close_pool() -> None:
    pool.close()


def rows(sql: str, params: Any = None) -> list[dict]:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def one(sql: str, params: Any = None) -> dict | None:
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchone()


@contextmanager
def tx(actor: str = "ui") -> Iterator[psycopg.Cursor]:
    """One transaction, one cursor. Sets stockintel.actor so audit triggers record who wrote."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT set_config('stockintel.actor', %s, true)", (actor,))
        yield cur


def execute(sql: str, params: Any = None) -> int:
    """Run a write. Returns affected row count."""
    with pool.connection() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount


def ping() -> bool:
    try:
        with pool.connection(timeout=5) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1 AS ok")
            return cur.fetchone()["ok"] == 1
    except Exception as e:  # noqa: BLE001 - health probe must never raise
        log.warning("db ping failed: %s", e)
        return False
