# Kubernetes deployment

Compose is the reference deploy (`docker compose --profile intraday up -d`). These manifests are
for running the recorder on a Kubernetes cluster instead, as an always-on single-replica
Deployment. Read-only market data. No orders, no broker mutation. Single-writer is enforced two
ways: one replica with the `Recreate` strategy, and the DB lease in `intraday.ingestion_jobs`
(authoritative through any rollover or accidental double-run).

## What it needs

- Image `stock-intel/intraday-recorder:0.1.0`, delivered to the node (no registry required).
- ConfigMap `intraday-recorder-config`: non-secret runtime knobs (feed, symbols, poll, lease).
- Secret `intraday-recorder-secrets`: `STOCKS_INTRADAY_SVC_PASSWORD`, `ALPACA_API_KEY_ID`,
  `ALPACA_API_SECRET_KEY`. `config.py` reads them from the environment, which is what the
  Secret injects. See `deploy/k8s/secret.example.yaml` for the shape.
- Egress to Postgres (`STOCKS_PG_HOST:5432`) and to Alpaca (`data.alpaca.markets`,
  `paper-api.alpaca.markets`).

## Build (amd64)

    cd stock-intel
    docker build --provenance=false --sbom=false --platform linux/amd64 \
      -f intraday/deploy/Dockerfile -t stock-intel/intraday-recorder:0.1.0 .
    docker save stock-intel/intraday-recorder:0.1.0 -o intraday-recorder-0.1.0.tar

`--provenance=false --sbom=false` avoids the Docker 29 multi-arch manifest list + attestations,
which `ctr images import` does not handle cleanly.

## Deliver + deploy (on the node)

    ctr images import intraday-recorder-0.1.0.tar             # -> docker.io/stock-intel/intraday-recorder:0.1.0
    kubectl apply -f deploy/k8s/recorder.yaml                        # namespace + configmap + deployment
    # Create the Secret from values you hold locally, then remove the file. Never print them.
    kubectl apply -f secret.live.yaml && shred -u secret.live.yaml

The Deployment uses `imagePullPolicy: Never` (the image is present on the node, not pulled).

## Verify

    kubectl -n intraday get pods -o wide
    kubectl -n intraday logs deploy/intraday-recorder --tail=30

Expect `recorder up: session=<uuid> feed=iex symbols=N market_open=...` then `poll: bars+N quotes+N ...`.
The `session=<uuid>` equals `uuid_generate_v5(uuid_ns_url(),'intraday:<date>')`, the same id WF-01
writes on the manifest, so recorder bars and the session manifest line up.

## Probes

Three probes, two endpoints, on purpose:

| Probe | Path | Touches the database | If it fails |
|---|---|---|---|
| startup | `/health/live` | no | the container is restarted, after a 60s budget |
| readiness | `/health` | yes, but see below | the pod leaves the Service |
| liveness | `/health/live` | no | the container is restarted |

**Do not point liveness at `/health`.** A liveness probe that pings Postgres turns a database
outage into a restart loop: the probe blocks, kubelet calls the process hung, the container is
killed, and the new one cannot reach the database either. Restarting an app has never fixed a
database on another host.

**`timeoutSeconds` has to exceed `db.ping`'s own wait** (2s), or a merely slow database reads as a
hung process. That single default, 1s against a 5s pool wait, is what caused the restart loop this
split was written to prevent.

**Readiness does not actually drop the pod when the database is down**, and that is deliberate.
`/health` answers 200 with `"db": false` rather than failing, because this deployment runs one
replica: pulling it from the Service would replace a degraded page that says what is wrong with no
page at all. The flag is there for humans and for monitoring. If you run more than one replica and
would rather route around a bad one, make `/health` return 503 when `db` is false and readiness
starts doing what its name suggests.

**The startup probe is not decoration.** When Postgres is unreachable the app spends about 30
seconds trying to open its pool before it binds the port, which is longer than the liveness initial
delay. Without a startup probe, a cold start during a database outage is itself a restart loop.

## Upgrade

Bump the tag (e.g. `0.1.1`), rebuild + save + `ctr images import`, update the image in
`deploy/k8s/recorder.yaml`, `kubectl apply`. `Recreate` tears down the old pod before the new one starts,
so the lease never sees two live writers.

## Not yet

- Recorder still records the fixed `SI_INTRADAY_SYMBOLS` list, not the WF-01 frozen movers
  universe. Wiring the recorder to read today's frozen `session_manifests.symbols` is a later
  iteration.
- Websocket streaming (`alpaca-py StockDataStream`) replaces the REST poll for lower latency.
