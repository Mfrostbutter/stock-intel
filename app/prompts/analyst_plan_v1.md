Date: {as_of}. Task: {question}

The brief the pipeline wrote:

<brief trust="untrusted">
{brief}
</brief>

One-line summary of today's score rows, holdings, benchmarks and run footer:

{summary}

The tickers picked for closer research today, by rule (holdings first, then entry flags, then risk flags, then the largest movers): {tickers}

Two things to return. claims_to_source: the statements in the brief that need a database source before they can be repeated, one per entry, each naming its ticker and number. focus: one or two sentences on what today's analysis should answer. Nothing else.
