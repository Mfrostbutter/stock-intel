"""Structured outputs for the analyst graph. Every claim carries citation ids."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

DISCLAIMER = (
    "Framework, not advice. Generated from stored data and cited sources; "
    "nothing here is a recommendation and nothing here places orders."
)


class Claim(BaseModel):
    """One sentence with the evidence ids that support it and any price levels it names."""
    text: str = Field(description="One sentence.")
    cites: list[str] = Field(default_factory=list, description="Evidence ids this sentence rests on.")
    levels: list[float] = Field(default_factory=list,
                                description="Every price or level number named in the text, as numbers.")


class EntryZone(BaseModel):
    low: float
    high: float


class HoldingView(BaseModel):
    ticker: str
    stance: Literal["hold", "add", "trim", "watch"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: list[Claim] = Field(default_factory=list)
    invalidation: Claim


class EntryView(BaseModel):
    ticker: str
    setup: str = Field(description="Flag name or a short setup label.")
    entry_zone: EntryZone
    invalidation: Claim
    horizon: Literal["days", "weeks", "months"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: list[Claim] = Field(default_factory=list)


class AvoidView(BaseModel):
    ticker: str
    reason: Claim


class AnalysisDraft(BaseModel):
    """What the draft model returns. Disclaimer and status are added in code."""
    market_read: list[Claim] = Field(description="2-3 sentences on regime and what changed.")
    holdings: list[HoldingView] = Field(default_factory=list)
    entries: list[EntryView] = Field(default_factory=list)
    avoid: list[AvoidView] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list,
                                 description="What the analyst wanted to know but the evidence did not contain.")
    data_gaps: list[str] = Field(default_factory=list,
                                 description="Sources that returned nothing today, from the run footer.")


class EntrySignalView(BaseModel):
    """One entry-watch ticker: is now the time, and why."""
    ticker: str
    signal: Literal["enter", "near", "wait", "avoid"]
    confidence: float = Field(ge=0.0, le=1.0)
    summary: str = Field(description="One plain sentence for the table row. No citations here.")
    rationale: list[Claim] = Field(default_factory=list, description="Two to four cited sentences.")
    invalidation: Claim
    zone_check: Literal["holds", "raise", "lower", "propose", "unclear"] = Field(
        description="Whether the user's zone still makes sense against the evidence; propose when the row has no zone.")
    suggested_zone: EntryZone | None = Field(default=None, description="Required when zone_check is propose, raise or lower.")
    suggested_invalidation: float | None = Field(default=None,
                                                 description="Price below which the proposed zone is wrong. With propose.")
    watch_for: list[str] = Field(default_factory=list, description="What would change this call. Plain strings.")


class EntrySignalDraft(BaseModel):
    """What the entry-signal draft returns. Disclaimer is added in code."""
    signals: list[EntrySignalView] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list,
                                 description="Evidence that was missing for the tickers under review.")


class PlanNotes(BaseModel):
    """Plan step output. The ticker list is built by code; the model adds these."""
    claims_to_source: list[str] = Field(default_factory=list,
                                        description="Statements in the brief that need a database source before they can be repeated.")
    focus: str = Field(default="", description="One or two sentences on what today's analysis should answer.")


class WorkPlan(BaseModel):
    """Plan step output: where to dig before drafting."""
    research: list[str] = Field(default_factory=list,
                                description="Tickers to pull history, news and levels for, most important first. At most 8.")
    claims_to_source: list[str] = Field(default_factory=list,
                                        description="Statements in the brief that need a database source before they can be repeated.")
    focus: str = Field(default="", description="One or two sentences on what today's analysis should answer.")


class CritiqueIssue(BaseModel):
    text: str = Field(description="The claim text, copied exactly.")
    reason: str


class Critique(BaseModel):
    unsupported: list[CritiqueIssue] = Field(default_factory=list,
                                             description="Claims whose cited evidence does not say what the claim says.")
