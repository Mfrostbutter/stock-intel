Review a draft analysis against the evidence it cites. You are checking facts and direction, not wording.

Draft (JSON):

{draft}

The cited evidence, and only the cited evidence:

{index}

A claim is unsupported only when one of these holds:
- a number in the claim does not appear in the cited items within 15 percent (rounding is fine: 2.2 percent for 0.0225 is a match),
- the direction is wrong (the claim says up, the evidence says down; above versus below a level),
- the claim names a fact, event or ticker that none of the cited items contains.

Everything else passes. Do not flag word choice, causal phrasing that joins two cited facts, a moving average called an average, "near" or "just below" used loosely, or a claim that is merely incomplete. When in doubt, the claim holds.

Return each unsupported claim with its text copied exactly and a one-line reason naming the missing or contradicting fact. Return an empty list when everything holds.
