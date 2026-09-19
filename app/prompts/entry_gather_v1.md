Entry-signal gather step for {as_of}. Task: {question}
Focus: {focus}

Tickers under review: {tickers}
{claims}

Each ticker has a db:entry: item in the evidence: the user's entry zone, invalidation level, horizon, thesis, and a rule-layer read (signal, distance to the zone, trend, RSI band, flags). score_history and price_window for every ticker are already loaded.

Pull what the rule layer cannot see: news_for_ticker for every ticker (7 days, then 30 if the 7 are empty), flag_history for any ticker whose setup repeats, earnings when a report date is near, insider, recs and fundamentals if rows exist. Use calc for every distance or percent you intend to state.

Budget is limited. Batch several tool calls per turn. When you have enough, reply with the single word DONE.
