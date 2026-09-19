# Disclaimer

**This is not investment advice.** Stock Intel is a research framework. It collects public market
data, scores it with rules you can read and change, and writes a daily brief. It does not know
your situation, your tax position or your risk tolerance, and it is not a substitute for a
licensed adviser. Every decision made with it is yours.

**Nothing here places an order.** No part of this project connects to a broker for trading. The
Alpaca credentials it uses are for market data and the market clock, and the setup instructions
say to use a paper account. A few database tables carry names from a paper-trading design
(`decisions`, `order_events`, `fills`); nothing in this repository writes them. Contributions
that add order placement, amendment or cancellation are out of scope and will be declined.

**The scores are opinions expressed as arithmetic.** Momentum, value, sentiment and catalyst are
weighted sums of inputs that were available that morning, from providers that are sometimes late,
sometimes wrong and sometimes silent. A component is left null rather than guessed when less than
half its inputs are present, which is honest but also means a composite can shift because coverage
changed rather than because anything happened. Read the `reasons` behind a score before you trust
it.

**The analyst is a language model.** It cites the tool results behind each claim and a second
model strikes uncited ones, which reduces invention without eliminating it. Treat its output as a
prompt for your own thinking.

**Data provider terms are yours to keep.** Alpaca, Finnhub, Alpha Vantage and the SEC each have
their own terms, and the free tiers are for personal, non-redistribution use. This project serves
data to you on your own machine and exposes no public redistribution endpoint. If you put it on a
network where other people can reach it, that is your arrangement to square with them.

**Your keys, your bills.** API keys, LLM spend and any paid data plan are on your account. The
analyst has a daily spend cap (`analyst_daily_cap_usd`) that defaults low; check it before you
change models.

**No warranty.** The software is provided as is, under the MIT licence. See [LICENSE](LICENSE).
