Entry signals for {as_of}. Task: {question}
Focus: {focus}

Tickers under review, one signal each, no others: {tickers}

Evidence index. Cite ids from here and nowhere else, copied exactly, date suffix included (db:scores:ASTS:2026-09-14, never db:scores:ASTS). The rule-layer read you quote must match the rule.signal value in the db:entry item word for word.

{index}

For every ticker under review return one signal:
- signal: enter when price is inside a zone you can defend and nothing in the evidence argues against acting now; near when the setup is forming but price or confirmation is not there yet; wait when the thesis holds but the entry does not; avoid when the thesis is broken, a risk flag is live, or price is below the invalidation.
- confidence 0 to 1, low when the evidence is thin or the rule read and your read disagree.
- summary: one plain sentence a table row can show. No citation ids in it.
- rationale: two to four sentences, each cited, each with any price it names in levels. Say when the rule-layer read (db:entry:) and yours differ and why.
- invalidation: one sentence naming the level that breaks the thesis, level in levels. State the condition directly; the word Invalidation is added by the renderer.
- zone_check: holds when the user's zone still makes sense; raise or lower when the evidence argues for a different zone, with suggested_zone low and high as grounded levels; propose when the db:entry item has no zone (zone_low and zone_high are null); unclear only when the data is too thin to say.
- Add rows (the db:entry item says ADD to a holding): the user already owns it and wants a dip that lowers the average cost. The cost basis is hidden from you; code clips the zone top below it. Propose the dip zone from technical support below the current price (a moving average being retested, a recent swing low, the 52-week low), not from where price is now. enter only when price is inside that dip zone; never enter on an add row because the trend is strong.
- When you propose: suggested_zone is the price range you would buy in, anchored to cited levels (a moving average, a recent swing low, the 52-week low, a prior support close), low and high within 15 percent of a cited level, and suggested_invalidation is a price below the zone where the idea is wrong. One rationale sentence must say what the zone is anchored to. The summary must begin with "Proposed zone". A proposed zone becomes the row's zone; the user can edit it.
- watch_for: one to three short plain strings naming what would change the call (a level, an event, a data source landing).

Only the data sources with rows count. Fundamentals, insider, earnings and filings may be empty; list them in data_gaps rather than guessing. Do not invent numbers.
