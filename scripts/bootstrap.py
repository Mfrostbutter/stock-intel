#!/usr/bin/env python3
"""One-shot setup for a fresh install: secrets, migrations, watchlist, n8n import, activation.

Order of a first run:
    cp .env.example .env
    python scripts/bootstrap.py --generate-secrets      # fills the blank passwords and keys
    docker compose up -d                                # postgres, n8n, app
    # open http://localhost:5678, create the owner account, create an API key, paste it in .env
    python scripts/bootstrap.py                         # everything else

Re-runnable. Nothing here prints a secret.

Usage: python scripts/bootstrap.py [--generate-secrets] [--intraday] [--skip-n8n] [--skip-seed]
"""
import json
import pathlib
import re
import secrets as stdlib_secrets
import subprocess
import sys
import time

import secrets_env

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
IDS = json.loads((ROOT / "deploy" / "n8n" / "ids.json").read_text(encoding="utf-8"))
GENERATED = [
    "POSTGRES_PASSWORD", "STOCKS_PG_PASSWORD", "STOCKS_APP_PG_PASSWORD", "STOCKS_ANALYST_PG_PASSWORD",
    "STOCKS_INTRADAY_SVC_PASSWORD", "STOCKS_INTRADAY_RO_PASSWORD", "N8N_DB_PASSWORD",
    "N8N_ENCRYPTION_KEY", "STOCK_INTEL_APP_TOKEN", "STOCK_INTEL_N8N_WEBHOOK_SECRET",
]
REQUIRED = ["POSTGRES_PASSWORD", "STOCKS_PG_PASSWORD", "STOCKS_APP_PG_PASSWORD",
            "FINNHUB_API_KEY", "ALPACA_API_KEY_ID", "ALPACA_API_SECRET_KEY"]


def run(args, **kw):
    return subprocess.run(args, cwd=ROOT, text=True, encoding="utf-8", errors="replace", **kw)


def compose(*args, **kw):
    return run(["docker", "compose", *args], **kw)


# -- secrets -----------------------------------------------------------------

def generate_secrets():
    if not ENV_PATH.exists():
        ENV_PATH.write_text((ROOT / ".env.example").read_text(encoding="utf-8"), encoding="utf-8")
        print("wrote .env from .env.example")
    text = ENV_PATH.read_text(encoding="utf-8")
    filled = []
    for name in GENERATED:
        pattern = re.compile(rf"^{name}=\s*$", re.M)
        if pattern.search(text):
            text = pattern.sub(f"{name}={stdlib_secrets.token_hex(24)}", text, count=1)
            filled.append(name)
    ENV_PATH.write_text(text, encoding="utf-8")
    ENV_PATH.chmod(0o600)
    print(f"generated {len(filled)} value(s): {', '.join(filled) or 'none, all were already set'}")
    print("Next: fill in your API keys in .env, then `docker compose up -d`.")


def check_env():
    missing = [n for n in REQUIRED if not secrets_env.get(n)]
    if missing:
        raise SystemExit("missing in .env: " + ", ".join(missing) +
                         "\nRun `python scripts/bootstrap.py --generate-secrets` and add your API keys.")


# -- database ----------------------------------------------------------------

def wait_for_postgres(timeout=120):
    import psycopg2
    host = secrets_env.get("STOCKS_PG_HOST", "localhost")
    deadline = time.time() + timeout
    while True:
        try:
            psycopg2.connect(host=host, port=int(secrets_env.get("STOCKS_PG_PORT", "5432")),
                             dbname="postgres", user=secrets_env.get("POSTGRES_USER", "postgres"),
                             password=secrets_env.require("POSTGRES_PASSWORD"),
                             connect_timeout=5).close()
            print(f"postgres reachable on {host}")
            return
        except Exception as e:
            if time.time() > deadline:
                raise SystemExit(f"postgres not reachable on {host}: {e}")
            time.sleep(3)


def migrate():
    r = run([sys.executable, str(ROOT / "scripts" / "migrate.py")])
    if r.returncode != 0:
        raise SystemExit("migrations failed")


# Model defaults per provider. A local model does all three jobs; the paid providers split them.
LLM_DEFAULTS = {
    "anthropic": {"name": "Anthropic", "base_url": "https://api.anthropic.com/v1", "key_env": "ANTHROPIC_API_KEY",
                  "model": "claude-sonnet-5", "deep": "claude-opus-5", "cheap": "claude-haiku-4-5-20251001"},
    "openai": {"name": "OpenAI", "base_url": "https://api.openai.com/v1", "key_env": "OPENAI_API_KEY",
               "model": "gpt-5.6-luna", "deep": "gpt-5.6-luna", "cheap": "gpt-5.6-luna"},
    "ollama": {"name": "Ollama", "base_url": None, "key_env": "OLLAMA_API_KEY",
               "model": "qwen3:14b", "deep": "qwen3:14b", "cheap": "qwen3:14b"},
}
# Values written by the migrations. Anything else means the user chose it, so bootstrap leaves it.
SHIPPED_MODEL_DEFAULTS = {"anthropic/claude-sonnet-5", "anthropic/claude-opus-5", "anthropic/claude-haiku-4.5",
                          "claude-sonnet-5", "claude-opus-5"}


def seed_llm_provider():
    """Point the analyst at LLM_PROVIDER, unless the models were already chosen in Settings."""
    import json as _json

    import psycopg2

    kind = (secrets_env.get("LLM_PROVIDER", "") or "").lower()
    if kind not in LLM_DEFAULTS:
        return
    d = LLM_DEFAULTS[kind]
    base = d["base_url"] or (secrets_env.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/") + "/v1")
    conn = psycopg2.connect(host=secrets_env.get("STOCKS_PG_HOST", "localhost"),
                            port=int(secrets_env.get("STOCKS_PG_PORT", "5432")),
                            dbname=secrets_env.get("STOCKS_PG_DB", "stocks"),
                            user=secrets_env.get("POSTGRES_USER", "postgres"),
                            password=secrets_env.require("POSTGRES_PASSWORD"),
                            options="-c search_path=intel,public", connect_timeout=10)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SELECT key, value FROM intel.config WHERE key IN "
                    "('analyst_providers','analyst_model','analyst_deep_model','analyst_cheap_model')")
        cfg = dict(cur.fetchall())
        registry = cfg.get("analyst_providers") or []
        if not any(r.get("id") == kind for r in registry):
            registry.append({"id": kind, "name": d["name"], "kind": kind, "base_url": base,
                             "key_env": d["key_env"], "enabled": True})
            cur.execute("UPDATE intel.config SET value = %s::jsonb, updated_at = now() WHERE key = 'analyst_providers'",
                        (_json.dumps(registry),))
        for key, ref in (("analyst_model", d["model"]), ("analyst_deep_model", d["deep"]),
                         ("analyst_cheap_model", d["cheap"])):
            if cfg.get(key) in SHIPPED_MODEL_DEFAULTS:
                cur.execute("UPDATE intel.config SET value = %s::jsonb, updated_at = now() WHERE key = %s",
                            (_json.dumps(f"{kind}:{ref}"), key))
    conn.close()
    print(f"analyst provider set to {kind}")


def seed_watchlist():
    seeder = ROOT / "scripts" / "seed_watchlist.py"
    if not seeder.exists():
        return
    if not (ROOT / "watchlist.yaml").exists():
        print("no watchlist.yaml, skipping seed (copy watchlist.example.yaml to start)")
        return
    if run([sys.executable, str(seeder)]).returncode != 0:
        raise SystemExit("watchlist seed failed")


# -- n8n ---------------------------------------------------------------------

def credential_payload():
    """The credential set the workflows bind to, built from .env. Values never printed."""
    g = secrets_env.get
    creds = IDS["credentials"]

    def entry(name, data):
        return {"id": creds[name]["id"], "name": name, "type": creds[name]["type"], "data": data}

    out = [
        entry("Stock Intel Postgres (stocks_n8n)", {
            "host": g("N8N_PG_HOST", "postgres"), "port": int(g("N8N_PG_PORT", "5432")),
            "database": g("STOCKS_PG_DB", "stocks"), "user": "stocks_n8n",
            "password": secrets_env.require("STOCKS_PG_PASSWORD"),
            "ssl": "disable", "allowUnauthorizedCerts": False}),
        entry("Stock Intel Alpaca (headers)", {"json": json.dumps({"headers": {
            "APCA-API-KEY-ID": secrets_env.require("ALPACA_API_KEY_ID"),
            "APCA-API-SECRET-KEY": secrets_env.require("ALPACA_API_SECRET_KEY")}})}),
        entry("Stock Intel Finnhub (query token)",
              {"name": "token", "value": secrets_env.require("FINNHUB_API_KEY")}),
    ]
    av = g("ALPHA_VANTAGE_API_KEY")
    if av:
        out.append(entry("Stock Intel Alpha Vantage (query apikey)", {"name": "apikey", "value": av}))
    hook = g("STOCK_INTEL_N8N_WEBHOOK_SECRET")
    if hook:
        out.append(entry("Stock Intel App Webhook (header)",
                         {"name": "X-Stock-Intel-Secret", "value": hook}))
    bot = g("TELEGRAM_BOT_TOKEN")
    if bot:
        out.append(entry("Stock Intel Telegram",
                         {"accessToken": bot, "baseUrl": "https://api.telegram.org"}))
    return out


def import_credentials():
    creds = credential_payload()
    payload = json.dumps(creds)
    # Written inside the container only, mode 600, removed straight after the import.
    r = compose("exec", "-T", "n8n", "sh", "-c",
                "umask 077 && cat > /tmp/si-credentials.json", input=payload)
    if r.returncode != 0:
        raise SystemExit("could not stage credentials in the n8n container")
    r = compose("exec", "-T", "n8n", "n8n", "import:credentials", "--input=/tmp/si-credentials.json")
    compose("exec", "-T", "n8n", "rm", "-f", "/tmp/si-credentials.json")
    if r.returncode != 0:
        raise SystemExit("n8n import:credentials failed")
    print(f"imported {len(creds)} credential(s)")


def for_import(wf):
    """The shipped JSON tags workflows by name. import:workflow wants tag objects, and skips
    (leaving a null tagId) anything without a .name, so expand the strings."""
    tags = [{"name": t} if isinstance(t, str) else t for t in (wf.get("tags") or [])]
    return {**wf, "tags": tags}


def import_workflows():
    paths = sorted((ROOT / "workflows").glob("*.json"))
    for path in paths:
        wf = for_import(json.loads(path.read_text(encoding="utf-8")))
        staged = f"/tmp/si-wf-{path.name}"
        r = compose("exec", "-T", "n8n", "sh", "-c", f"umask 077 && cat > {staged}",
                    input=json.dumps(wf))
        if r.returncode != 0:
            raise SystemExit(f"could not stage {path.name} in the n8n container")
        r = compose("exec", "-T", "n8n", "n8n", "import:workflow", f"--input={staged}")
        compose("exec", "-T", "n8n", "rm", "-f", staged)
        if r.returncode != 0:
            raise SystemExit(f"n8n import:workflow failed for {path.name}")
    print(f"imported {len(paths)} workflow(s)")


def activate(intraday=False):
    names = list(IDS["activate"]) + (list(IDS["activate_intraday"]) if intraday else [])
    for name in names:
        wid = IDS["workflows"][name]
        # publish:workflow is 2.x for "make the current version the active one". update:workflow
        # still works but is deprecated.
        r = compose("exec", "-T", "n8n", "n8n", "publish:workflow", f"--id={wid}")
        if r.returncode != 0:
            raise SystemExit(f"could not activate {name}")
        print(f"active  {name}")
    # Triggers and crons register at start-up, so the activations need a restart to take effect.
    compose("restart", "n8n")


def main():
    if "--generate-secrets" in sys.argv:
        generate_secrets()
        return
    check_env()
    wait_for_postgres()
    migrate()
    seed_llm_provider()
    if "--skip-seed" not in sys.argv:
        seed_watchlist()
    if "--skip-n8n" not in sys.argv:
        import_credentials()
        import_workflows()
        activate(intraday="--intraday" in sys.argv)
    print("\nbootstrap complete.")
    print(f"  app  http://localhost:{secrets_env.get('PORT', '8095')}/ui   (paste STOCK_INTEL_APP_TOKEN once)")
    print(f"  n8n  http://localhost:{secrets_env.get('N8N_HOST_PORT', '5678')}")
    print("  first Daily run: 06:00 on the next weekday, or trigger it from the app's Runs screen.")


if __name__ == "__main__":
    main()
