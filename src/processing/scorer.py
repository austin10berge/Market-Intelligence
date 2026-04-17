"""Signal scoring — assigns directional scores to raw signals."""

from __future__ import annotations

from ..models import ScoredSignal, Signal, SignalDirection, SignalSource


def score_signal(signal: Signal) -> ScoredSignal:
    """Score a raw signal as bullish (+1), neutral (0), or bearish (-1)."""
    scorer = _SCORERS.get(signal.source)
    if scorer is None:
        return ScoredSignal(
            signal=signal,
            score=0,
            direction=SignalDirection.NEUTRAL,
            reasoning=f"No scoring rules defined for {signal.source.value}",
        )
    return scorer(signal)


def _score_fear_greed(signal: Signal) -> ScoredSignal:
    """Fear & Greed: <25 bearish, >75 bullish (contrarian at extremes)."""
    score_val = signal.value
    extreme = score_val < 20 or score_val > 80

    if score_val <= 25:
        # Extreme fear is contrarian bullish for theta sellers
        return ScoredSignal(
            signal=signal,
            score=-1,
            direction=SignalDirection.BEARISH,
            extreme=extreme,
            reasoning=f"F&G at {score_val} — fear zone, market stressed",
        )
    elif score_val >= 75:
        return ScoredSignal(
            signal=signal,
            score=1,
            direction=SignalDirection.BULLISH,
            extreme=extreme,
            reasoning=f"F&G at {score_val} — greed zone, watch for reversal",
        )
    else:
        return ScoredSignal(
            signal=signal,
            score=0,
            direction=SignalDirection.NEUTRAL,
            extreme=False,
            reasoning=f"F&G at {score_val} — neutral range",
        )


def _score_vix(signal: Signal) -> ScoredSignal:
    """VIX: <15 bullish (complacent), >25 bearish (stress), backwardation amplifies."""
    vix = signal.value
    structure = signal.metadata.get("term_structure", "Unknown")
    extreme = vix < 12 or vix > 30

    # Base score from VIX level
    if vix <= 15:
        score, direction = 1, SignalDirection.BULLISH
        reasoning = f"VIX {vix} — low vol, calm market"
    elif vix >= 25:
        score, direction = -1, SignalDirection.BEARISH
        reasoning = f"VIX {vix} — elevated vol, stress"
    else:
        score, direction = 0, SignalDirection.NEUTRAL
        reasoning = f"VIX {vix} — moderate"

    # Backwardation is an additional stress signal
    if structure == "Backwardation":
        if score >= 0:
            score = -1
            direction = SignalDirection.BEARISH
        reasoning += " | Backwardation — near-term stress exceeds long-term"
    elif structure == "Contango":
        reasoning += " | Contango — normal/calm term structure"

    return ScoredSignal(
        signal=signal,
        score=score,
        direction=direction,
        extreme=extreme,
        reasoning=reasoning,
    )


def _score_put_call(signal: Signal) -> ScoredSignal:
    """Put/Call: >1.2 contrarian bullish (extreme fear), <0.7 bearish (complacency)."""
    ratio = signal.value
    extreme = ratio > 1.2 or ratio < 0.7

    if ratio > 1.2:
        # Extreme put buying — contrarian bullish signal
        return ScoredSignal(
            signal=signal,
            score=1,
            direction=SignalDirection.BULLISH,
            extreme=extreme,
            reasoning=f"P/C {ratio} — extreme put buying, contrarian buy zone",
        )
    elif ratio < 0.7:
        return ScoredSignal(
            signal=signal,
            score=-1,
            direction=SignalDirection.BEARISH,
            extreme=extreme,
            reasoning=f"P/C {ratio} — excessive call buying, complacency warning",
        )
    else:
        return ScoredSignal(
            signal=signal,
            score=0,
            direction=SignalDirection.NEUTRAL,
            extreme=False,
            reasoning=f"P/C {ratio} — normal range",
        )


def _score_sector_etf(signal: Signal) -> ScoredSignal:
    """Sector rotation: defensive leading = bearish, cyclical leading = bullish."""
    rotation = signal.metadata.get("rotation", "Neutral rotation")
    rotation_spread = signal.metadata.get("rotation_spread", 0.0)
    extreme = abs(rotation_spread) > 1.5

    if "Risk-off" in rotation:
        return ScoredSignal(
            signal=signal,
            score=-1,
            direction=SignalDirection.BEARISH,
            extreme=extreme,
            reasoning=f"Sector rotation: defensive leading by {rotation_spread:+.2f}%",
        )
    elif "Risk-on" in rotation:
        return ScoredSignal(
            signal=signal,
            score=1,
            direction=SignalDirection.BULLISH,
            extreme=extreme,
            reasoning=f"Sector rotation: cyclical leading by {abs(rotation_spread):.2f}%",
        )
    else:
        return ScoredSignal(
            signal=signal,
            score=0,
            direction=SignalDirection.NEUTRAL,
            extreme=False,
            reasoning="Sector rotation: neutral, no clear tilt",
        )


def _score_gex(signal: Signal) -> ScoredSignal:
    """GEX: > $5B bullish (pinning), < $0 bearish (volatile)."""
    gex_billions = signal.value
    extreme = gex_billions < 0 or gex_billions > 10

    if gex_billions < 0:
        return ScoredSignal(
            signal=signal,
            score=-1,
            direction=SignalDirection.BEARISH,
            extreme=extreme,
            reasoning=f"GEX at ${gex_billions:+.2f}B — negative gamma, high volatility risk",
        )
    elif gex_billions > 5:
        return ScoredSignal(
            signal=signal,
            score=1,
            direction=SignalDirection.BULLISH,
            extreme=extreme,
            reasoning=f"GEX at ${gex_billions:+.2f}B — positive gamma, price pinning expected",
        )
    else:
        return ScoredSignal(
            signal=signal,
            score=0,
            direction=SignalDirection.NEUTRAL,
            extreme=False,
            reasoning=f"GEX at ${gex_billions:+.2f}B — neutral range",
        )


def _score_credit_spreads(signal: Signal) -> ScoredSignal:
    """Credit Spreads: > 5.0% bearish/stress, < 3.5% bullish/complacent."""
    spread = signal.value
    extreme = spread > 6.0 or spread < 3.0

    if spread > 5.0:
        return ScoredSignal(
            signal=signal, score=-1, direction=SignalDirection.BEARISH, extreme=extreme,
            reasoning=f"Credit spread at {spread:.2f}% — high stress, risk-off",
        )
    elif spread < 3.5:
        return ScoredSignal(
            signal=signal, score=1, direction=SignalDirection.BULLISH, extreme=extreme,
            reasoning=f"Credit spread at {spread:.2f}% — tight spreads, risk-on",
        )
    else:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
            reasoning=f"Credit spread at {spread:.2f}% — normal range",
        )


def _score_liquidity(signal: Signal) -> ScoredSignal:
    """Liquidity scoring placeholder (requires trend analysis / rolling averages)."""
    net_liq = signal.metadata["display_trillions"]
    return ScoredSignal(
        signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
        reasoning=f"Net Liquidity at ${net_liq:.2f}T (requires trend tracking for directional score)",
    )


# Registry of scoring functions
_SCORERS = {
    SignalSource.FEAR_GREED: _score_fear_greed,
    SignalSource.VIX: _score_vix,
    SignalSource.PUT_CALL: _score_put_call,
    SignalSource.SECTOR_ETF: _score_sector_etf,
    SignalSource.GEX: _score_gex,
    SignalSource.CREDIT_SPREADS: _score_credit_spreads,
    SignalSource.LIQUIDITY: _score_liquidity,
}
