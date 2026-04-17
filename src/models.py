"""Data models for signals, scores, and digests."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field


class SignalDirection(str, Enum):
    """Directional scoring for a signal."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"


class SignalSource(str, Enum):
    """Available data sources for Phase 1."""

    FEAR_GREED = "fear_greed"
    VIX = "vix"
    PUT_CALL = "put_call"
    SECTOR_ETF = "sector_etf"


class Signal(BaseModel):
    """Raw signal data from a fetcher."""

    source: SignalSource
    timestamp: datetime = Field(default_factory=datetime.now)
    value: float = Field(description="Primary numeric value (e.g., F&G score, VIX level)")
    metadata: dict = Field(
        default_factory=dict,
        description="Additional data points (e.g., term structure, sector breakdown)",
    )
    summary: str = Field(default="", description="Human-readable summary of the signal")


class ScoredSignal(BaseModel):
    """A signal with directional scoring applied."""

    signal: Signal
    score: int = Field(ge=-1, le=1, description="+1 bullish, 0 neutral, -1 bearish")
    direction: SignalDirection
    extreme: bool = Field(default=False, description="Whether this reading is at an extreme")
    reasoning: str = Field(default="", description="Why this score was assigned")


class MarketPosture(str, Enum):
    """Overall market posture assessment."""

    STRONGLY_BULLISH = "Strongly Bullish"
    BULLISH = "Bullish"
    CAUTIOUSLY_BULLISH = "Cautiously Bullish"
    NEUTRAL = "Neutral"
    CAUTIOUSLY_BEARISH = "Cautiously Bearish"
    BEARISH = "Bearish"
    STRONGLY_BEARISH = "Strongly Bearish"


class Digest(BaseModel):
    """The final evening digest delivered to the user."""

    date: date = Field(default_factory=date.today)
    posture: MarketPosture
    composite_score: float = Field(description="Aggregate score from all signals")
    scored_signals: list[ScoredSignal] = Field(default_factory=list)
    llm_summary: str = Field(default="", description="LLM-generated ~150 word digest")
    raw_text: str = Field(default="", description="Full formatted notification text")
