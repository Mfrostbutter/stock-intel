# The analyst

An optional LLM pass over data that is already in your database. It reads, it cites, and it cannot
write anything. The scores and the brief work fine without it.

Built with LangGraph. The graph itself is a pure factory (`app/stockintel/analyst/graph.py`) with
effects injected from `service.py`, so the whole thing is unit-tested without a model or a database.

## The graphs

**Daily analysis**, fired by the pipeline after the brief is stored:

```
plan -> gather (tool loop) -> draft -> critique (cheap model) -> finalize
                                  \-> optional human review before finalize
```

- **plan** decides what to look at, given the day's scores.
- **gather** runs the tool loop: read rows, read more rows, stop.
- **draft** writes the commentary, with a citation on every claim.
- **critique** runs on the cheap model and strikes anything not grounded in a tool result.
- **finalize** stores the analysis, its sources and its cost.

`analyst_hitl` pauses before finalize so you approve in the UI. Worth leaving on for the first week
with any new model.

**Entry pass**, fired four times a trading day by the Entry Signals workflow: `entry_gather ->
entry_draft` over the entry-watch list, re-checking zones against today's prices.

## Tools

Ten, all bound in `tools.py`:

| Tool | Returns |
|---|---|
| `score_history` | composite and components over time for a ticker |
| `price_window` | closes, returns and indicators for a window |
| `news_for_ticker` | headlines with sentiment and source |
| `flag_history` | when a flag fired before, and what happened next |
| `earnings` | past and upcoming dates, surprises |
| `fundamentals` | the stored snapshot |
| `insider` | open-market buys and sells |
| `recs` | analyst rating distribution over time |
| `db_sql` | one SELECT, checked by a parser, 500 rows, five calls a run |
| `calc` | arithmetic through a restricted AST, because models are bad at mental maths |

Caps: 500 rows per query, 60 rows handed to the model, 30 template calls and 5 raw SQL calls per
run. The caps exist to bound cost and to stop a confused loop from reading the whole database one
row at a time.

## Why it cannot hurt you

- The analyst connects as `stocks_analyst`: SELECT only, `default_transaction_read_only=on`, a
  5-second statement timeout. Even a perfect prompt injection through a news headline has nothing
  to write with.
- `db_sql` parses the statement and refuses anything that is not a single SELECT.
- All retrieved text is data, never instructions. The system prompt says so, the critic enforces
  citations, and the database role makes the question moot.

## Prompts

`app/prompts/analyst_{system,plan,gather,draft,critique}_v1.md` and
`entry_{gather,draft}_v1.md`. They are markdown files you can edit. The sha256 of the whole set is
stored on every `analyses` row, so a prompt change is visible in the history rather than being a
mystery drift in output quality.

## Providers and models

The registry lives in `intel.config.analyst_providers`; the Settings screen edits it. Four kinds:
`anthropic`, `openai`, `ollama`, `openrouter`. A provider row names the environment variable that
carries its key and never the key itself, and the UI only learns whether that variable is set.

Three roles, three config rows: `analyst_model` (draft), `analyst_deep_model` (the opt-in deep
pass), `analyst_cheap_model` (the critic). A model reference is `provider:model`, so
`anthropic:claude-sonnet-5` and `ollama:qwen3:14b` can coexist.

`scripts/bootstrap.py` seeds sensible defaults from `LLM_PROVIDER` on a fresh install and leaves
anything you have chosen alone afterwards.

## Cost

Every run records tokens and cost on `analyses`. The day's total is checked against
`analyst_daily_cap_usd` before each run and again mid-run; when the cap is reached the analyst
stops for the day and the pipeline carries on without it. `/api/analyst/spend` and the Spend panel
show where you are. Ollama reports zero, because it is.

## Feedback

Thumbs and a note from either UI land in `analysis_feedback`. `model_evals` holds bake-off runs if
you want to compare models on the same day's data; the bake-off list ships empty.
