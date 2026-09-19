# Deploy

The reference deploy is Docker Compose: Postgres, n8n and the app on one machine, with the
intraday recorder as an opt-in profile. Kubernetes manifests live in [`deploy/k8s/`](../deploy/k8s)
for people who already run a cluster.

## What runs

| Service | Image | Port | Notes |
|---|---|---|---|
| `postgres` | `postgres:16` | 5432 on loopback | databases `stocks` and `n8n`, one volume |
| `n8n` | `n8nio/n8n:2.37.7` | 5678 on loopback | its own database, `workflows/` mounted read-only |
| `app` | built from `app/` | 8095 on loopback | FastAPI, the SPA, the analyst |
| `recorder` | built from `intraday/` | none | profile `intraday`, off by default |

Published ports bind to `HOST_BIND_IP`, which is `127.0.0.1` out of the box. Postgres is published
so the scripts in `scripts/` can reach it from the host.

## First run

```bash
cp .env.example .env
python scripts/bootstrap.py --generate-secrets
```

That fills every blank password, the n8n encryption key, the app bearer token and the webhook
secret. Then put your API keys in the same file (see [SETUP-APIS.md](SETUP-APIS.md)) and choose a
watchlist:

```bash
cp watchlist.example.yaml watchlist.yaml     # edit it
docker compose up -d
```

Open `http://localhost:5678`, create the n8n owner account, then Settings, n8n API, create an API
key and paste it into `.env` as `N8N_API_KEY`. That is the one click the bootstrap cannot do for
you: n8n has no unattended way to create the first account.

```bash
python scripts/bootstrap.py
```

which, in order: waits for Postgres, applies every migration, seeds the analyst provider from
`LLM_PROVIDER`, seeds the watchlist and fires the two-year price backfill, writes the n8n
credentials, imports all twelve workflows with their pinned ids, activates the daily set and
restarts n8n so the schedules register.

Finally open `http://localhost:8095/ui` and paste `STOCK_INTEL_APP_TOKEN` once. The browser keeps
it; the app has no login page and no users, just the one token.

## The schedules

| Workflow | When | Timezone |
|---|---|---|
| Daily | 06:00, Monday to Friday | `TZ` in `.env` |
| Entry Signals | 10:00, 12:00, 14:00, 15:45 on weekdays | same |
| Intraday (opt-in) | premarket prep, every minute, every 5 minutes | same |

The crons were written for US market hours, so set `TZ=America/New_York` unless you have a reason
not to. The pipeline scores the last completed session, so a Monday run reports Friday.

## The intraday profile

```bash
docker compose --profile intraday up -d
python scripts/bootstrap.py --intraday
```

That starts the recorder and activates the three intraday workflows. One of them runs every minute,
so it stays off until you ask for it. See [INTRADAY.md](INTRADAY.md).

## Exposing it

Don't, without help. The app authenticates with a single bearer token and nothing else, and n8n's
UI is behind its own login but was never meant to face the internet. If you need access from
elsewhere, put a reverse proxy with real authentication in front, or reach the machine over a
private network (Tailscale, WireGuard, a VPN) and leave the bind on loopback.

To listen on a LAN address instead, set `HOST_BIND_IP=0.0.0.0` and understand that anyone on that
network now needs only the bearer token.

## Backups

Two things matter:

- **the Postgres volume**: every price, score, brief and analysis. `docker compose exec -T postgres
  pg_dump -U postgres stocks | gzip > stocks-$(date +%F).sql.gz` is enough.
- **`.env`**: it holds `N8N_ENCRYPTION_KEY`. Lose that and n8n cannot decrypt its stored
  credentials, so you would re-run `scripts/bootstrap.py` to write them again.

The workflows themselves live in git, so a rebuild is `docker compose up -d` plus
`python scripts/bootstrap.py`.

## Everyday commands

```bash
docker compose ps                       # what is up
docker compose logs -f app              # app log
docker compose logs -f n8n              # pipeline log
python scripts/migrate.py --status      # which migrations are applied
python scripts/seed_watchlist.py        # re-seed after editing watchlist.yaml
python scripts/db_query.py "SELECT ticker, composite FROM scores_daily WHERE as_of = (SELECT max(as_of) FROM scores_daily) ORDER BY composite DESC LIMIT 10"
```

## Upgrades

Pinned versions and the upgrade path are in [UPGRADING.md](UPGRADING.md). The short version:
`git pull`, `docker compose up -d --build`, `python scripts/migrate.py`.

## Troubleshooting

| Symptom | Usually |
|---|---|
| `bootstrap.py` says a value is missing | a blank in `.env`; run `--generate-secrets` and add your API keys |
| n8n import fails with "connection refused" | the stack is not up yet; `docker compose up -d` and retry |
| The brief has no scores | the watchlist is empty, or the first backfill has not finished. Check Runs |
| `value` and `catalyst` are null on day one | coverage under 50%; they fill in as fundamentals and signals land |
| Workflows imported but nothing fires | activation needs the n8n restart bootstrap does at the end; re-run it |
| The app returns 401 | the bearer token in the browser does not match `STOCK_INTEL_APP_TOKEN` |
