#!/usr/bin/env python3
"""Run one SQL statement against the stocks DB as stocks_n8n and print rows.

Reads: STOCKS_PG_* from .env (never printed).
Usage: python scripts/db_query.py "SELECT ..."   (or - to read SQL from stdin)
"""
import sys

import psycopg2

import secrets_env


def main():
    sql = sys.stdin.read() if sys.argv[1] == "-" else sys.argv[1]
    conn = psycopg2.connect(**secrets_env.pg())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(sql)
        if cur.description:
            cols = [c[0] for c in cur.description]
            print(" | ".join(cols))
            for row in cur.fetchall():
                print(" | ".join("" if v is None else str(v) for v in row))
        else:
            print(f"rows affected: {cur.rowcount}")
    conn.close()


if __name__ == "__main__":
    main()
