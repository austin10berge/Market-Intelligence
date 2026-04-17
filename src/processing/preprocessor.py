"""Preprocessor — normalize signals and compute composite scores."""

from __future__ import annotations

from ..models import MarketPosture, ScoredSignal


def compute_composite_score(scored_signals: list[ScoredSignal]) -> float:
    """Compute a composite score from all scored signals.

    Returns a value from -1.0 (strongly bearish) to +1.0 (strongly bullish).
    """
    if not scored_signals:
        return 0.0

    total = sum(s.score for s in scored_signals)
    return round(total / len(scored_signals), 3)


def determine_posture(composite_score: float, scored_signals: list[ScoredSignal]) -> MarketPosture:
    """Map a composite score + signal context to an overall market posture."""
    extremes = sum(1 for s in scored_signals if s.extreme)

    if composite_score >= 0.75:
        return MarketPosture.STRONGLY_BULLISH
    elif composite_score >= 0.4:
        return MarketPosture.BULLISH
    elif composite_score >= 0.15:
        return MarketPosture.CAUTIOUSLY_BULLISH
    elif composite_score > -0.15:
        # Check for conflicting extremes — uncertainty = neutral
        if extremes >= 2:
            return MarketPosture.NEUTRAL  # mixed signals with extremes
        return MarketPosture.NEUTRAL
    elif composite_score > -0.4:
        return MarketPosture.CAUTIOUSLY_BEARISH
    elif composite_score > -0.75:
        return MarketPosture.BEARISH
    else:
        return MarketPosture.STRONGLY_BEARISH
