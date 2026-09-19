#!/usr/bin/env python3
"""Secret and config lookup for scripts and the recorder. Environment first, secret manager optional.

Named secrets_env, not secrets, so it never shadows the standard library module.

A normal install keeps every value in .env at the repo root. A deployment that already runs a
secret manager sets STOCK_INTEL_SECRET_CMD to a command that prints one secret to stdout, with
{name} where the secret name goes, and anything missing from the environment is fetched with it.
Values are never printed.
"""
from __future__ import annotations

import functools
import os
import shlex
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env_file():
    path = Path(os.environ.get("STOCK_INTEL_ENV_FILE", REPO_ROOT / ".env"))
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:  # minimal installs: parse the simple KEY=value form
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return
    load_dotenv(path)


_load_env_file()


@functools.lru_cache(maxsize=64)
def _from_secret_cmd(name):
    """Fetch one secret with STOCK_INTEL_SECRET_CMD, e.g. 'vault kv get -field={name} secret/stock-intel'."""
    template = os.environ.get("STOCK_INTEL_SECRET_CMD")
    if not template:
        return None
    args = [a.replace("{name}", name) for a in shlex.split(template)]
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       env=dict(os.environ, MSYS_NO_PATHCONV="1"))
    return r.stdout.strip() or None


def get(name, default=None):
    """Value of `name` from the environment, else the secret command when set, else `default`."""
    v = os.environ.get(name)
    if v:
        return v.strip()
    v = _from_secret_cmd(name)
    if v:
        return v
    return default


def require(name):
    """Same as get(), but exits when the value is missing."""
    v = get(name)
    if not v:
        raise SystemExit(f"{name} is not set (put it in .env at the repo root)")
    return v


def pg(user=None, password_name=None, search_path="intel,public", dbname=None):
    """psycopg connect kwargs for one of the stocks roles. Defaults to the pipeline role."""
    # Scripts run as the pipeline role, not the app role in STOCKS_PG_USER.
    user = user or get("STOCKS_PIPELINE_PG_USER", "stocks_n8n")
    password_name = password_name or {
        "stocks_n8n": "STOCKS_PG_PASSWORD",
        "stocks_app": "STOCKS_APP_PG_PASSWORD",
        "stocks_analyst": "STOCKS_ANALYST_PG_PASSWORD",
        "stocks_intraday_svc": "STOCKS_INTRADAY_SVC_PASSWORD",
        "stocks_intraday_ro": "STOCKS_INTRADAY_RO_PASSWORD",
    }.get(user, "STOCKS_PG_PASSWORD")
    return dict(host=get("STOCKS_PG_HOST", "postgres"),
                port=int(get("STOCKS_PG_PORT", "5432")),
                dbname=dbname or get("STOCKS_PG_DB", "stocks"),
                user=user,
                password=require(password_name),
                options=f"-c search_path={search_path}",
                connect_timeout=10)


def sec_user_agent():
    """SEC requires a contact string on every request. See docs/SETUP-APIS.md."""
    return require("SEC_USER_AGENT")
