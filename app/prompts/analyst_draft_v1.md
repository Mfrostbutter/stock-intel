Write the analysis for {as_of}. Task: {question}
Focus: {focus}

Allowed tickers: {tickers}

Evidence index. Cite ids from here and nowhere else:

{index}

Write:
- market_read: two or three sentences on regime and what changed, each cited (benchmark and run-footer ids).
- holdings: one entry per ticker that has a db:holding id. Stance hold, add, trim or watch. Two to four rationale sentences, each cited. One invalidation sentence naming a level, with the level in levels; state the condition directly ("A close below 135.99 ..."), the word Invalidation is added by the renderer.
- entries: tickers carrying an entry flag today, or a setup you can defend from price_window and score_history. entry_zone low and high must be grounded levels. Invalidation names a level. Horizon days, weeks or months.
- avoid: tickers carrying a risk flag or a broken setup, one cited reason each.
- questions: what you wanted and the evidence did not contain.
- data_gaps: sources that returned nothing today, from the run footer.

Confidence is 0 to 1 and should be low when the evidence is thin. Do not invent numbers.
