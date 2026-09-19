# Upgrading

## Pinned versions

| Piece | Pin | Why |
|---|---|---|
| n8n | `2.37.7` | the workflows are exported from this version and the deploy path is tested against it |
| Postgres | `16` | no version-specific SQL, but the volume format is |
| Python images | `3.13-slim` | app and recorder |

Pins live in `compose.yml`. Moving any of them is a deliberate act, not a `:latest`.

## The routine upgrade

```bash
git pull
docker compose up -d --build
python scripts/migrate.py
```

`migrate.py` applies whatever is new and skips what is applied. If a workflow changed upstream,
re-import it:

```bash
python scripts/bootstrap.py --skip-seed
```

which rewrites the credentials, re-imports every workflow at its pinned id and re-activates the
daily set. It is safe to re-run; the ids are stable, so this updates workflows in place rather than
making duplicates.

## Moving n8n

Read the release notes for anything between your version and the target, particularly node type
version changes. Then:

1. Back up the volume: `docker compose exec -T postgres pg_dump -U postgres n8n | gzip > n8n-backup.sql.gz`.
2. Change the tag in `compose.yml`, `docker compose up -d n8n`.
3. Watch the log; n8n migrates its own schema on start.
4. Open each workflow once and confirm it still publishes. A node type version bump can change a
   parameter shape, and the editor is where that shows up.
5. If anything moved, fix the JSON in the repo, not in the UI, and redeploy with
   `scripts/deploy_workflow.py`.

The workflows are plain JSON in git, so the worst case is re-importing them into a fresh instance.

## Moving Postgres

A major version bump means a dump and restore, not an in-place volume swap:

```bash
docker compose exec -T postgres pg_dumpall -U postgres | gzip > all-$(date +%F).sql.gz
docker compose down
docker volume rm stock-intel_pg          # only after the dump is verified
# bump the image tag, then
docker compose up -d postgres
gunzip -c all-*.sql.gz | docker compose exec -T postgres psql -U postgres
```

## Changing the scoring

Edit the `Score v2` node, bump `SCORE_VERSION` inside it, redeploy. Old rows keep their version, so
you can compare before and after on the same days. `scripts/backtest_flags.py` measures forward
returns by flag over your backfilled history, which is the honest way to decide whether the change
was an improvement.

## Changing models

Settings, Models. Nothing to redeploy. The three roles (draft, deep, critic) can point at different
providers. Watch the spend panel for a day after a change.

## Breaking changes to expect

This project is young. Migrations are additive and the workflow ids are stable, so upgrades should
be boring, but:

- a new migration can add a column the app expects, so upgrade the app and the schema together;
- if a collector's output shape changes, the brief's status check changes with it;
- the analyst prompt set is versioned by hash, so a prompt change shows up in `analyses` history
  rather than silently altering output.

Read the commit messages between your version and the new one. They are written to be read.
