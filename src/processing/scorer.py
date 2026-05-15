"""Signal scoring — assigns directional scores to raw signals."""

from __future__ import annotations

from ..models import ScoredSignal, Signal, SignalDirection, SignalSource


def score_signal(signal: Signal, context: dict | None = None) -> ScoredSignal:
    """Score a raw signal, returning a continuous score in [-1.0, +1.0].

    Args:
        signal:  The raw signal to score.
        context: Optional cross-signal context for regime-aware scorers.
                 Currently recognised keys:
                   - "vix" (float): current VIX spot level, used by the P/C scorer
                     to suppress contrarian signals in trending/stressed markets.
    """
    scorer = _SCORERS.get(signal.source)
    if scorer is None:
        return ScoredSignal(
            signal=signal,
            score=0.0,
            direction=SignalDirection.NEUTRAL,
            reasoning=f"No scoring rules defined for {signal.source.value}",
        )
    return scorer(signal, context or {})


def check_convergence(scored_signals: list) -> list[str]:
    """Detect tickers where insiders AND politicians are both buying.

    Returns a list of convergence alert strings for the LLM prompt.
    """
    insider = next(
        (s for s in scored_signals if s.signal.source.value == "insider_trading"), None
    )
    congress = next(
        (s for s in scored_signals if s.signal.source.value == "congressional_trades"), None
    )
    if not insider or not congress:
        return []

    insider_buys = set(insider.signal.metadata.get("buy_tickers", {}).keys())
    congress_buys = set(congress.signal.metadata.get("buys_by_ticker", {}).keys())
    overlap = insider_buys & congress_buys

    alerts = []
    for ticker in sorted(overlap):
        insider_count = len(insider.signal.metadata["buy_tickers"].get(ticker, []))
        congress_count = len(congress.signal.metadata["buys_by_ticker"].get(ticker, []))
        alerts.append(
            f"🚨 CONVERGENCE: {ticker} — {insider_count} exec insider buy(s) + "
            f"{congress_count} congressional purchase(s) in same window"
        )

    return alerts

def _score_fear_greed(signal: Signal, context: dict) -> ScoredSignal:
    """Fear & Greed: continuous linear score centred at 50 (neutral).

    Score formula: (value - 50) / 25 → +1.0 at F&G=75, -1.0 at F&G=25.
    High F&G (greed) is bullish for CSP sellers; low F&G (fear) is bearish.
    """
    score_val = signal.value
    extreme = score_val < 20 or score_val > 80

    NEUTRAL = 50.0
    NORM_SPAN = 25.0
    raw = (score_val - NEUTRAL) / NORM_SPAN
    score = round(max(-1.0, min(1.0, raw)), 3)

    THRESHOLD = 0.15
    if score > THRESHOLD:
        direction = SignalDirection.BULLISH
        desc = "greed zone" if score_val >= 75 else "greed bias"
    elif score < -THRESHOLD:
        direction = SignalDirection.BEARISH
        desc = "fear zone" if score_val <= 25 else "fear bias"
    else:
        direction = SignalDirection.NEUTRAL
        desc = "neutral"

    return ScoredSignal(
        signal=signal,
        score=score,
        direction=direction,
        extreme=extreme,
        reasoning=f"F&G at {score_val} — {desc} → score {score:+.3f}",
    )


def _score_vix(signal: Signal, context: dict) -> ScoredSignal:
    """VIX: continuous linear score centred at 20 (neutral midpoint).

    Score formula: (20 - vix) / 5 → +1.0 at VIX=15, -1.0 at VIX=25.
    Backwardation applies a -0.5 floor (overrides bullish readings).
    """
    vix = signal.value
    structure = signal.metadata.get("term_structure", "Unknown")
    extreme = vix < 12 or vix > 30

    NEUTRAL_VIX = 20.0
    NORM_SPAN = 5.0
    raw = (NEUTRAL_VIX - vix) / NORM_SPAN
    raw = max(-1.0, min(1.0, raw))

    if structure == "Backwardation":
        raw = min(raw, -0.5)
        structure_note = " | Backwardation — near-term stress exceeds long-term"
    elif structure == "Contango":
        structure_note = " | Contango — normal/calm term structure"
    else:
        structure_note = ""

    score = round(raw, 3)

    THRESHOLD = 0.15
    if score > THRESHOLD:
        direction = SignalDirection.BULLISH
    elif score < -THRESHOLD:
        direction = SignalDirection.BEARISH
    else:
        direction = SignalDirection.NEUTRAL

    reasoning = f"VIX {vix} → score {score:+.3f}{structure_note}"

    return ScoredSignal(
        signal=signal,
        score=score,
        direction=direction,
        extreme=extreme,
        reasoning=reasoning,
    )


def _score_put_call(signal: Signal, context: dict) -> ScoredSignal:
    """Put/Call ratio — continuous sentiment factor with regime-aware weighting.

    Mental model:
        Signal  = direction   (which way is sentiment leaning?)
        Extreme = conviction  (is this a tradeable edge, not just noise?)

    Continuous score formula:
        Neutral anchor is P/C = 1.0 (neither fear nor greed).
        score = clip( (ratio - 1.0) / 0.5, -1.0, +1.0 )

        Examples:
            P/C = 0.50 → raw score = -1.0  (max complacency / bearish warning)
            P/C = 0.70 → raw score = -0.6
            P/C = 1.00 → raw score =  0.0  (neutral)
            P/C = 1.30 → raw score = +0.6  (mild fear / mild bullish bias)
            P/C = 1.50 → raw score = +1.0  (capped)
            P/C = 2.00 → raw score = +1.0  (capped, but extreme=True)

    Extreme flag (conviction / timing edge — separate from direction):
        extreme_high: ratio > 1.8  — deep fear, potential reversal setup
        extreme_low:  ratio < 0.55 — deep complacency, elevated reversal risk

    Regime weighting (VIX-based):
        Contrarian signals are weakest in trending/stressed markets and
        strongest when the market is range-bound or in late-stage moves.

        VIX context rules (applied to the *contrarian bullish* side only):
            VIX > 28 (sustained stress / trend):   weight = 0.4 — suppress bullishness
            20 < VIX <= 28 (transition / caution):  weight = 0.7 — partial weight
            VIX <= 20 (calm / range-bound):          weight = 1.0 — full weight

        The bearish side (low P/C = complacency) is not suppressed by high VIX;
        complacency in a low-vol environment is still a valid warning signal.
    """
    ratio = signal.value
    rolling_avg = signal.metadata.get("rolling_avg_5d")

    # ── 1. Continuous raw score ───────────────────────────────────────────────
    # Normalisation span: ±0.5 from neutral anchor maps to the full ±1.0 range.
    # Readings beyond ±0.5 are clamped — their *extremeness* is captured below.
    NEUTRAL_ANCHOR = 1.0
    NORMALISATION_SPAN = 0.5
    raw_score = (ratio - NEUTRAL_ANCHOR) / NORMALISATION_SPAN
    raw_score = max(-1.0, min(1.0, raw_score))  # clip to [-1, +1]

    # ── 2. Extreme flag — conviction / timing edge ───────────────────────────
    # Intentionally wider thresholds than the old hard buckets so that
    # 'extreme' means genuinely rare, not just slightly elevated.
    extreme = ratio > 1.8 or ratio < 0.55

    # ── 3. Regime weighting — suppress contrarian bullishness in stress ───────
    # Only applies to the bullish (positive) side; bearish complacency signals
    # do not need regime suppression.
    vix = context.get("vix")
    regime_note = ""
    regime_weight = 1.0

    if raw_score > 0 and vix is not None:
        if vix > 28:
            regime_weight = 0.4
            regime_note = f" | Regime: stressed (VIX {vix:.1f}) — contrarian signal suppressed"
        elif vix > 20:
            regime_weight = 0.7
            regime_note = f" | Regime: transitional (VIX {vix:.1f}) — reduced weight"
        else:
            regime_note = f" | Regime: calm (VIX {vix:.1f}) — full contrarian weight"
    elif vix is None and raw_score > 0:
        regime_note = " | Regime: unknown (no VIX context) — unweighted"

    weighted_score = round(raw_score * regime_weight, 3)

    # ── 4. Derive direction from the *weighted* score ─────────────────────────
    # Use a small dead-zone around zero to avoid noisy NEUTRAL flip-flopping.
    DIRECTION_THRESHOLD = 0.15
    if weighted_score > DIRECTION_THRESHOLD:
        direction = SignalDirection.BULLISH
    elif weighted_score < -DIRECTION_THRESHOLD:
        direction = SignalDirection.BEARISH
    else:
        direction = SignalDirection.NEUTRAL

    # ── 5. Build human-readable reasoning ────────────────────────────────────
    rolling_str = f" | 5d avg: {rolling_avg:.3f}" if rolling_avg else ""

    if ratio > 1.8:
        signal_desc = "extreme put buying — deep fear, potential reversal setup"
    elif ratio > 1.3:
        signal_desc = "elevated put buying — fear bias, contrarian bullish lean"
    elif ratio > 1.0:
        signal_desc = "mild put skew — slight fear, modest bullish tilt"
    elif ratio > 0.75:
        signal_desc = "mild call skew — slight complacency, modest bearish tilt"
    elif ratio > 0.55:
        signal_desc = "elevated call buying — complacency building"
    else:
        signal_desc = "extreme call buying — deep complacency, elevated reversal risk"

    reasoning = (
        f"P/C {ratio:.3f} → raw score {raw_score:+.2f}"
        f" × regime weight {regime_weight:.1f} = {weighted_score:+.3f}"
        f" | {signal_desc}{rolling_str}{regime_note}"
    )

    return ScoredSignal(
        signal=signal,
        score=weighted_score,
        direction=direction,
        extreme=extreme,
        reasoning=reasoning,
    )


def _score_sector_etf(signal: Signal, context: dict) -> ScoredSignal:
    """Sector rotation: continuous score based on rotation_spread magnitude.

    rotation_spread = defensive_avg - cyclical_avg (from fetcher).
    Positive spread → defensives leading → risk-off → bearish (negative score).
    Negative spread → cyclicals leading → risk-on → bullish (positive score).
    Score formula: -rotation_spread / 1.5, clipped to [-1, +1].
    """
    rotation_spread = signal.metadata.get("rotation_spread", 0.0)
    extreme = abs(rotation_spread) > 1.5

    NORM_SPAN = 1.5
    raw = -rotation_spread / NORM_SPAN
    score = round(max(-1.0, min(1.0, raw)), 3)

    THRESHOLD = 0.15
    if score > THRESHOLD:
        direction = SignalDirection.BULLISH
        desc = f"cyclical leading ({-rotation_spread:+.2f}%)"
    elif score < -THRESHOLD:
        direction = SignalDirection.BEARISH
        desc = f"defensive leading ({rotation_spread:+.2f}%)"
    else:
        direction = SignalDirection.NEUTRAL
        desc = f"neutral rotation ({rotation_spread:+.2f}%)"

    return ScoredSignal(
        signal=signal,
        score=score,
        direction=direction,
        extreme=extreme,
        reasoning=f"Sector rotation: {desc} → score {score:+.3f}",
    )


def _score_gex(signal: Signal, context: dict) -> ScoredSignal:
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


def _score_credit_spreads(signal: Signal, context: dict) -> ScoredSignal:
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


def _score_liquidity(signal: Signal, context: dict) -> ScoredSignal:
    """Liquidity scoring placeholder (requires trend analysis / rolling averages)."""
    net_liq = signal.metadata["display_trillions"]
    return ScoredSignal(
        signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
        reasoning=f"Net Liquidity at ${net_liq:.2f}T (requires trend tracking for directional score)",
    )


def _score_insider_trading(signal: Signal, context: dict) -> ScoredSignal:
    """Insider trading: scaled by ticker count, asymmetric buy/sell weighting.

    Buys reach max signal at 2+ tickers (execs buy for one reason: conviction).
    Sells reach max signal at 4+ tickers (execs sell for many reasons; dampened).
    net = buy_count - sell_count:
        positive → score = min(+1.0, net / 2.0)
        negative → score = max(-1.0, net / 4.0)
    """
    buy_count = signal.metadata.get("buy_ticker_count", 0)
    sell_count = signal.metadata.get("sell_ticker_count", 0)
    total_buys = signal.metadata.get("total_buys", 0)
    total_sells = signal.metadata.get("total_sells", 0)
    extreme = (buy_count >= 3 and sell_count == 0) or (sell_count >= 3 and buy_count == 0)

    if total_buys == 0 and total_sells == 0:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
            reasoning="Insider Trading: no open-market activity this week",
        )

    net = buy_count - sell_count
    if net > 0:
        raw = min(1.0, net / 2.0)
    elif net < 0:
        raw = max(-1.0, net / 4.0)
    else:
        raw = 0.0

    score = round(raw, 3)

    THRESHOLD = 0.15
    if score > THRESHOLD:
        direction = SignalDirection.BULLISH
    elif score < -THRESHOLD:
        direction = SignalDirection.BEARISH
    else:
        direction = SignalDirection.NEUTRAL

    return ScoredSignal(
        signal=signal, score=score, direction=direction, extreme=extreme,
        reasoning=f"Insider Trading: {buy_count} buy ticker(s), {sell_count} sell ticker(s) → score {score:+.3f}",
    )


def _score_congressional_trades(signal: Signal, context: dict) -> ScoredSignal:
    """Congressional trades: net purchases = bullish, net sales = bearish."""
    buy_count = signal.metadata.get("buy_ticker_count", 0)
    sell_count = signal.metadata.get("sell_ticker_count", 0)
    total_buys = signal.metadata.get("total_buys", 0)
    total_sells = signal.metadata.get("total_sells", 0)
    high_profile = signal.metadata.get("high_profile_trades", [])
    # Extreme if high-profile politicians are active OR 5+ tickers on one side
    extreme = len(high_profile) > 0 or buy_count >= 5 or sell_count >= 5

    if total_buys == 0 and total_sells == 0:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
            reasoning="Congressional Trades: no watchlist activity in disclosure window",
        )

    if buy_count > sell_count:
        return ScoredSignal(
            signal=signal, score=1, direction=SignalDirection.BULLISH, extreme=extreme,
            reasoning=f"Congressional Trades: purchases in {buy_count} ticker(s), sales in {sell_count}"
                      + (f" | High-profile: {high_profile[0]}" if high_profile else ""),
        )
    elif sell_count > buy_count:
        return ScoredSignal(
            signal=signal, score=-1, direction=SignalDirection.BEARISH, extreme=extreme,
            reasoning=f"Congressional Trades: sales in {sell_count} ticker(s), purchases in {buy_count}"
                      + (f" | High-profile: {high_profile[0]}" if high_profile else ""),
        )
    else:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=extreme,
            reasoning=f"Congressional Trades: balanced — {buy_count} buy, {sell_count} sell ticker(s)",
        )


def _score_unusual_volume(signal: Signal, context: dict) -> ScoredSignal:
    """Unusual volume: bullish if most spikes are on up days, bearish if down days."""
    spikes = signal.metadata.get("spikes", [])
    up = signal.metadata.get("up_spikes", 0)
    down = signal.metadata.get("down_spikes", 0)
    spike_count = signal.metadata.get("spike_count", 0)
    extreme = spike_count >= 4

    if spike_count == 0:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=False,
            reasoning="Unusual Volume: no spikes detected across watchlist",
        )

    # Find the highest ratio spike for reasoning
    top = spikes[0] if spikes else {}
    top_str = f" | Biggest: {top.get('symbol', '?')} at {top.get('ratio', 0):.1f}x avg" if top else ""

    if up > down:
        return ScoredSignal(
            signal=signal, score=1, direction=SignalDirection.BULLISH, extreme=extreme,
            reasoning=f"Unusual Volume: {spike_count} spike(s), {up} on up days vs {down} down days{top_str}",
        )
    elif down > up:
        return ScoredSignal(
            signal=signal, score=-1, direction=SignalDirection.BEARISH, extreme=extreme,
            reasoning=f"Unusual Volume: {spike_count} spike(s), {down} on down days vs {up} up days{top_str}",
        )
    else:
        return ScoredSignal(
            signal=signal, score=0, direction=SignalDirection.NEUTRAL, extreme=extreme,
            reasoning=f"Unusual Volume: {spike_count} spike(s) split evenly up/down{top_str}",
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
    SignalSource.INSIDER_TRADING: _score_insider_trading,
    SignalSource.CONGRESSIONAL_TRADES: _score_congressional_trades,
    SignalSource.UNUSUAL_VOLUME: _score_unusual_volume,
}
