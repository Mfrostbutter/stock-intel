Gather step for {as_of}. Task: {question}
Focus: {focus}

Tickers to research: {tickers}
Brief claims that need a source:
{claims}

score_history and price_window for every ticker above are already in the evidence below. Use the tools for what is missing: news_for_ticker for every holding and for any ticker where sentiment or a flag matters, flag_history when a setup repeats, earnings when a report date is near, insider, recs and fundamentals when the brief's Value or Cat column stands out for a ticker or the setup rests on them. Use calc for every distance or percent you intend to state. Use db_sql only when no template answers the question.

Budget is limited. Ask for what you need in as few turns as possible, batching several tool calls per turn. When you have enough, reply with the single word DONE.
