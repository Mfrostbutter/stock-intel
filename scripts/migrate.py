#!/usr/bin/env python3
"""Apply sql/*.sql in order and record each file in intel.schema_migrations.

Idempotent: a file already recorded with the same checksum is skipped. A recorded file whose
contents changed is an error, because the migrations are written to be edited forward, not in
place. Runs as the Postgres superuser, which is what the grants in the migrations need.

Usage: python scripts/migrate.py [--status] [--connect postgres://...]
"""
import hashlib
import pathlib
import sys

import secrets_env

SQL_DIR = pathlib.Path(__file__).resolve().parents[1] / "sql"
BOOTSTRAP = """
CREATE SCHEMA IF NOT EXISTS intel;
CREATE TABLE IF NOT EXISTS intel.schema_migrations (
  filename    text PRIMARY KEY,
  sha256      text NOT NULL,
  applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


def connect():
    import psycopg2

    dsn = None
    if "--connect" in sys.argv:
        dsn = sys.argv[sys.argv.index("--connect") + 1]
    if dsn:
        return psycopg2.connect(dsn, connect_timeout=10)
    # Superuser by default: the migrations create schemas and grant to the service roles.
    return psycopg2.connect(host=secrets_env.get("STOCKS_PG_HOST", "postgres"),
                            port=int(secrets_env.get("STOCKS_PG_PORT", "5432")),
                            dbname=secrets_env.get("STOCKS_PG_DB", "stocks"),
                            user=secrets_env.get("POSTGRES_USER", "postgres"),
                            password=secrets_env.require("POSTGRES_PASSWORD"),
                            connect_timeout=10)


def files():
    return sorted(p for p in SQL_DIR.glob("*.sql"))


def plan(done: dict, paths=None):
    """What each migration file needs: ('apply'|'skip', filename, sha256). Raises on a changed file."""
    out = []
    for f in (paths if paths is not None else files()):
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if done.get(f.name) == sha:
            out.append(("skip", f, sha))
        elif f.name in done:
            raise SystemExit(f"{f.name} changed after it was applied. Add a new migration instead.")
        else:
            out.append(("apply", f, sha))
    return out


def main():
    conn = connect()
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(BOOTSTRAP)
        cur.execute("SELECT filename, sha256 FROM intel.schema_migrations")
        done = dict(cur.fetchall())

    if "--status" in sys.argv:
        for f in files():
            sha = hashlib.sha256(f.read_bytes()).hexdigest()
            state = "applied" if done.get(f.name) == sha else ("CHANGED" if f.name in done else "pending")
            print(f"{state:8} {f.name}")
        return

    applied = 0
    for action, f, sha in plan(done):
        if action == "skip":
            continue
        sql = f.read_text(encoding="utf-8")
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.execute("INSERT INTO intel.schema_migrations (filename, sha256) VALUES (%s, %s)", (f.name, sha))
        print(f"applied {f.name}")
        applied += 1
    print(f"{applied} migration(s) applied, {len(files()) - applied} already current")
    conn.close()


if __name__ == "__main__":
    main()
