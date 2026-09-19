# Scoring

Every score is arithmetic over rows already in the database. No model is involved, nothing is
random, and a run can be recomputed from history. The code is one JS node, `Score v2` in
`workflows/daily.json`, and the output lands in `intel.scores_daily` with the inputs that produced
it in `reasons`.

Four components, each 0 to 100, each optional.

## Momentum

Starts at 50 and moves with trend, strength and participation:

| Input | Effect |
|---|---|
| close above / below SMA200 | ±10 |
| close above / below SMA50 | ±8 |
| SMA50 above / below SMA200 | ±7 |
| RSI(14) distance from 50 | ±15, half a point per RSI point |
| 20-day return minus the benchmark's | ±15 |
| volume z-score in the direction of today's move | ±5 |

Needs a close. Everything else contributes only if present.

## Sentiment

7-day mean news sentiment, scaled to 0 to 100, then damped by article count: one headline cannot
move a score, ten can. The delta against the 30-day baseline adds up to ±10. With no news the
component is null rather than 50.

## Value

Price against growth and quality, not a raw multiple. Six inputs, each a signed adjustment to 50:

| Input | Reads |
|---|---|
| revenue growth | above 10% helps, below hurts |
| P/E against growth, or P/S against growth when there are no earnings | cheap relative to what it is growing |
| gross margin | against a 40% anchor |
| operating margin | scaled |
| debt to equity | below 0.5 helps |
| current ratio | above 1.5 helps |

Fundamentals arrive from Finnhub's metrics endpoint. **Coverage rule**: with fewer than three of
the six present, value is null rather than a guess.

## Catalyst

What is about to happen, or just did. Six inputs again:

| Input | Reads |
|---|---|
| next earnings date | inside 14 days adds 8, inside 30 adds 4 |
| last earnings surprise | ±10, if it was within 45 days |
| news surge | today's article rate against the watchlist median, worth more when volume confirms |
| insider net flow | open-market buys minus sells over 90 days, log-scaled, buys count more than sells |
| analyst buy share | share of buy and strong-buy ratings, ±6 |
| shift in that share | against the previous month, ±6 |

Same rule: fewer than three inputs present, catalyst is null.

## Composite

```
composite = sum(component * weight) / sum(weight of present components)
```

Default weights, editable in Settings (`intel.config.weights`, no redeploy):

| Component | Weight |
|---|---|
| momentum | 0.30 |
| sentiment | 0.25 |
| value | 0.25 |
| catalyst | 0.20 |

Renormalising over present components is deliberate. A ticker with no fundamentals is still
comparable to one that has them, instead of being silently punished for the gap. The flip side is
that a composite can move because coverage changed rather than because anything happened, so
`coverage` ships on every row and the brief says when a component is missing.

## Entry flags

Flags are observations, not signals to act on. Each carries the evidence behind it.

| Flag | Rule |
|---|---|
| `pullback-in-uptrend` | close above SMA200, below SMA20, RSI 35 to 45, sentiment not negative |
| `oversold-bounce` | RSI below 30 with a volume z-score above 1.5 |
| `breakout` | close above the prior 20-day high, volume z above 2, sentiment not falling |
| `near-target` | within 3% of the `target_entry` you set on the watchlist |

## Risk flags

| Flag | Rule |
|---|---|
| `earnings-soon` | earnings within 7 calendar days, roughly five sessions |
| `breakout-exhaustion` | a breakout whose RSI is below its own 20-day high: price made a new high, strength did not |
| `sentiment-collapse` | 7-day sentiment more than 0.3 below the 30-day baseline |
| `down-50pct-from-52w-high` | what it says |
| `gap-up-10pct`, `gap-down-10pct` | a 10% move in a day |

## Changing it

The weights are config. The rest is one JS node you can read in about ten minutes; change it,
regenerate the canvas stickies, redeploy, and bump `SCORE_VERSION` so old rows stay
distinguishable. `scripts/backtest_flags.py` and `scripts/backtest_breakout_divergence.py` measure
forward returns by flag over whatever history you have backfilled, which is the honest way to
decide whether a change helped.

Benchmarks are priced and scored for context but never flagged, because a setup on an index fund is
not a setup.
