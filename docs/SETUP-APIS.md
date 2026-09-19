# API setup

Four data sources, one optional LLM provider, one optional push channel. Everything below has a
free tier; the only thing that can cost money is the analyst, and Ollama makes that free too.

Daily budget for a 50-ticker watchlist: Alpaca about 55 calls, Finnhub about 250, Alpha Vantage 22
(a hard budget the pipeline keeps to), SEC one call per new ticker, and one to three analyst runs.

Every value goes in `.env` at the repo root. `scripts/bootstrap.py` turns them into n8n credentials
for you; nothing is typed into the n8n UI.

## Alpaca: prices, market clock, intraday bars, quotes

1. Sign up at [alpaca.markets](https://alpaca.markets). A **paper** account is enough. No funding,
   no brokerage approval.
2. In the paper dashboard: API Keys, then Generate. Copy both halves into `.env`:
   `ALPACA_API_KEY_ID` and `ALPACA_API_SECRET_KEY`.
3. Market data plan: Basic, which is free and serves the IEX feed. SIP data is refused when it is
   less than 15 minutes old, so the price collector already asks for data up to 16 minutes ago and
   the recorder uses IEX minute bars. Leave `STOCK_INTEL_QUOTE_FEED=iex`. A paid plan unlocks
   `sip` and nothing else changes.
4. Limit: 200 requests a minute on Basic. The collectors batch symbols, so a daily run is about one
   call per ticker.
5. Used by: Collect-Prices (`/v2/stocks/bars`), the Daily and Entry Signals clock and calendar
   checks, the intraday workflows, the app's live quotes, and the health check.

**Without it** nothing prices, so the pipeline has nothing to score. This one is required.

## Finnhub: news, earnings, fundamentals, insiders, analyst ratings, ticker validation

1. [finnhub.io](https://finnhub.io), get a free API key, put it in `FINNHUB_API_KEY`.
2. Free tier: 60 calls a minute. Every endpoint used is on the free tier: `company-news`,
   `calendar/earnings`, `stock/metric?metric=all`, `stock/insider-transactions`,
   `stock/recommendation`, and `stock/profile2` for validating a ticker you add. The collectors
   pace themselves at roughly one call a second.
3. Company news reaches back about twelve months on the free tier. ETFs have no company profile,
   so they are validated by hand and skipped by the news collector.

**Without it** you lose sentiment, earnings dates, fundamentals and signals, so two of the four
score components go null. Required in practice.

## Alpha Vantage: topic sentiment

1. [alphavantage.co/support/#api-key](https://www.alphavantage.co/support/#api-key), into
   `ALPHA_VANTAGE_API_KEY`.
2. Free tier: 25 requests a day. Collect-News spends at most `quota.av_budget` (22 by default) and
   only on tickers that got no Finnhub sentiment that day. When the budget is spent the run still
   reports `ok` rather than degraded, because a spent quota is expected, not a failure.

**Without it** leave the key blank. Sentiment then comes from Finnhub alone, which is thinner for
small caps but perfectly workable.

## SEC EDGAR: CIK lookup, no key

Set `SEC_USER_AGENT` to something that identifies you, for example
`stock-intel your-name your@email.example`. The SEC fair-access policy requires a contact string on
every request and will block anonymous traffic. Used by `scripts/seed_watchlist.py` to fill
`watchlist.cik`. Filings ingestion is a roadmap item, not shipped.

## LLM provider: the analyst

Pick one in `LLM_PROVIDER`. `scripts/bootstrap.py` seeds the matching provider row and model
defaults; you can change models later in Settings. Costs land on your key, and
`analyst_daily_cap_usd` stops the analyst for the day when the meter is reached.

| `LLM_PROVIDER` | Key | Default models (draft / deep / critic) |
|---|---|---|
| `anthropic` | `ANTHROPIC_API_KEY` from console.anthropic.com | `claude-sonnet-5`, `claude-opus-5`, `claude-haiku-4-5-20251001` |
| `openai` | `OPENAI_API_KEY` from platform.openai.com; `OPENAI_BASE_URL` for a compatible host | `gpt-5.6-luna` |
| `ollama` | none. Install Ollama, `ollama pull qwen3:14b`, set `OLLAMA_BASE_URL` | `qwen3:14b` |
| `openrouter` | `OPENROUTER_API_KEY` | whatever you configure |

Notes:

- From inside Compose, a local Ollama is `http://host.docker.internal:11434`.
- The model must support tool calling. `qwen3:14b` is the smallest one tested end to end; smaller
  models tend to skip the tools and invent numbers, which the critic then strikes, which wastes the
  run. Keep `analyst_hitl` on for the first week with any new model.
- OpenRouter requests carry `provider.data_collection=deny`, enforced by a hook that refuses to
  send a request without it.

**What leaves your machine**: for the tickers being analysed, the analyst sends scores, news
snippets and fundamentals. Your position sizes are excluded unless
`analyst_share_positions_offsite` is turned on. With Ollama nothing leaves at all.

**Without a provider** the pipeline, scores and brief all work; only the analyst commentary is
missing.

## Telegram: optional push

1. Message [@BotFather](https://t.me/BotFather), `/newbot`, copy the token into
   `TELEGRAM_BOT_TOKEN`.
2. Send your new bot a message, then open
   `https://api.telegram.org/bot<token>/getUpdates` and read `message.chat.id`. Put it in
   `TELEGRAM_CHAT_ID`, or paste it into Settings later.
3. Bootstrap creates the `Stock Intel Telegram` credential. The brief arrives as a short message
   plus the full markdown as an attachment. Details in [NOTIFICATIONS.md](NOTIFICATIONS.md).

## LangSmith: optional tracing

`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`. Traces every analyst run,
which is useful while tuning prompts. Off when blank.

## Where the keys end up

`.env` on disk, mode 600, gitignored. From there:

- the app reads them as environment variables;
- `scripts/bootstrap.py` writes the n8n credentials once, inside the n8n container, and deletes the
  staging file straight after;
- nothing prints a value, and no key is ever stored in the database. A provider row names the
  environment variable, never the secret.

If you keep secrets in a manager already, set `STOCK_INTEL_SECRET_CMD` to a command that prints one
secret to stdout with `{name}` where the name goes, and leave those entries out of `.env`.
