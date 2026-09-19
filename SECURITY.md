# Security

## Reporting

Report a vulnerability through GitHub's private advisory form on this repository
(Security, Report a vulnerability). Please do not open a public issue for it.

Expect an acknowledgement within a few days. This is a personal project, so there is no formal SLA,
but anything that exposes a user's keys or data will be treated as the priority it is.

## The model this project assumes

It runs on your machine, for you. There are no users, no roles and no tenancy. Every port binds to
loopback by default, and the intended deployment is behind a private network or a proxy that does
real authentication.

## What protects what

**The app** takes a single bearer token, `STOCK_INTEL_APP_TOKEN`, on every `/api` route. It is
fail-closed: with no token configured the API refuses every request rather than falling open. The
UI stores it in the browser. It is not a login system, and it is not meant to face the internet.

**The database** has five roles with different rights. The analyst's role is the interesting one:
`stocks_analyst` is SELECT only, runs with `default_transaction_read_only=on` and a 5-second
statement timeout. So a prompt injection arriving through a news headline has nothing to write
with, and cannot hold a connection open either. The raw-SQL tool additionally parses each statement
and refuses anything that is not a single SELECT, with a row cap and a call cap per run.

**n8n and the app** authenticate to each other with a shared header secret,
`STOCK_INTEL_N8N_WEBHOOK_SECRET`, in both directions.

**Secrets** live in `.env` on the host, mode 600, gitignored. The n8n credentials are written once
inside the container from a file that is deleted immediately after import. No key is stored in the
database: a provider row names the environment variable and the UI only learns whether it is set.
Nothing in the codebase prints a secret value, and CI runs a secret scan on every push.

**Outbound data**: the analyst sends scores, news snippets and fundamentals for the tickers it
analyses to whichever LLM provider you chose. Position sizes are excluded unless you turn
`analyst_share_positions_offsite` on. OpenRouter requests carry `provider.data_collection=deny`,
enforced by a hook that refuses to send a request without it. With Ollama nothing leaves the
machine.

## If you expose it

Put something in front that authenticates properly, keep `HOST_BIND_IP` on loopback and reach it
over a private network, or both. n8n's editor in particular should never be public: it executes
code by design.

## Known limits

- One shared bearer token, no rotation, no audit of who used it.
- No rate limiting on the app; it assumes a single trusted caller.
- n8n's own auth is whatever n8n provides.
- Anyone who can read the Docker socket or the host filesystem can read `.env`.
