from __future__ import annotations

import logging
import math

import pandas as pd
import pandas_ta as ta

logger = logging.getLogger(__name__)

_MIN_BARS = 210  # enough for EMA200 + warmup


def compute_features(
    ticker: str,
    as_of_date: str,
    df: pd.DataFrame,
    sector: str | None = None,
) -> dict | None:
    """Compute all features for ticker as of as_of_date.

    df must be sorted ascending by DatetimeIndex. Data after as_of_date is ignored.
    Returns None if fewer than _MIN_BARS of history are available.
    """
    cutoff = pd.Timestamp(as_of_date)
    df = df[df.index <= cutoff].copy()

    if len(df) < _MIN_BARS:
        logger.debug("Insufficient history for %s on %s: %d bars", ticker, as_of_date, len(df))
        return None

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"]

    curr_close = float(close.iloc[-1])
    curr_volume = int(volume.iloc[-1])

    # ── EMAs ──────────────────────────────────────────────────────────────────
    ema20 = _last(ta.ema(close, length=20))
    ema50 = _last(ta.ema(close, length=50))
    ema150 = _last(ta.ema(close, length=150))
    ema200 = _last(ta.ema(close, length=200))

    # ── SMAs ──────────────────────────────────────────────────────────────────
    sma20 = _last(ta.sma(close, length=20))
    sma50 = _last(ta.sma(close, length=50))
    sma150 = _last(ta.sma(close, length=150))
    sma200 = _last(ta.sma(close, length=200))

    # ── RSI(14) ───────────────────────────────────────────────────────────────
    rsi = _last(ta.rsi(close, length=14))

    # ── ADX(14) — pandas_ta returns ADX_14, DMP_14, DMN_14 ───────────────────
    adx = None
    adx_df = ta.adx(high, low, close, length=14)
    if adx_df is not None and not adx_df.empty:
        adx_cols = [c for c in adx_df.columns if c.upper().startswith("ADX")]
        if adx_cols:
            adx = _last(adx_df[adx_cols[0]])

    # ── Bollinger Bands(20, 2σ) — pandas_ta returns BBL_, BBM_, BBU_, BBB_, BBP_ ──
    bb_upper = bb_middle = bb_lower = bb_pct_b = bb_width_pct = None
    price_above_bb_middle = price_above_bb_upper = price_below_bb_lower = None
    bb_df = ta.bbands(close, length=20, std=2.0)
    if bb_df is not None and not bb_df.empty:
        upper_cols = [c for c in bb_df.columns if c.startswith("BBU")]
        mid_cols = [c for c in bb_df.columns if c.startswith("BBM")]
        lower_cols = [c for c in bb_df.columns if c.startswith("BBL")]
        pct_b_cols = [c for c in bb_df.columns if c.startswith("BBP")]
        bw_cols = [c for c in bb_df.columns if c.startswith("BBB")]
        if upper_cols and mid_cols and lower_cols:
            bb_upper = _last(bb_df[upper_cols[0]])
            bb_middle = _last(bb_df[mid_cols[0]])
            bb_lower = _last(bb_df[lower_cols[0]])
            if pct_b_cols:
                bb_pct_b = _last(bb_df[pct_b_cols[0]])
            if bw_cols:
                bw_raw = _last(bb_df[bw_cols[0]])
                # pandas_ta BBB is already expressed as a % of middle band
                bb_width_pct = bw_raw
            if bb_upper is not None and bb_middle is not None and bb_lower is not None:
                price_above_bb_middle = int(curr_close > bb_middle)
                price_above_bb_upper = int(curr_close > bb_upper)
                price_below_bb_lower = int(curr_close < bb_lower)

    # ── MACD(12, 26, 9) — pandas_ta returns MACD_, MACDh_, MACDs_ ────────────
    macd_histogram = None
    macd_df = ta.macd(close, fast=12, slow=26, signal=9)
    if macd_df is not None and not macd_df.empty:
        hist_cols = [c for c in macd_df.columns if c.startswith("MACDh")]
        if hist_cols:
            macd_histogram = _last(macd_df[hist_cols[0]])

    # ── ROC(20) ───────────────────────────────────────────────────────────────
    roc20 = _last(ta.roc(close, length=20))

    # ── ATR(14) as % of close ─────────────────────────────────────────────────
    atr_pct = None
    atr_val = _last(ta.atr(high, low, close, length=14))
    if atr_val is not None and curr_close > 0:
        atr_pct = atr_val / curr_close * 100

    # ── RV-20 (annualized realized volatility) ────────────────────────────────
    rv20 = None
    ret = close.pct_change().dropna()
    if len(ret) >= 20:
        rv20 = float(ret.iloc[-20:].std() * math.sqrt(252))

    # ── Volume ratio vs 20-day avg (excluding today) ──────────────────────────
    volume_ratio = None
    vol_window = volume.iloc[-21:-1]
    if len(vol_window) >= 10:
        avg = float(vol_window.mean())
        if avg > 0:
            volume_ratio = curr_volume / avg

    # ── Distance from 52-week high ────────────────────────────────────────────
    lookback = df["High"].iloc[-252:] if len(df) >= 252 else df["High"]
    high_52wk = float(lookback.max())
    pct_from_52wk_high = ((high_52wk - curr_close) / high_52wk * 100) if high_52wk > 0 else None

    def _vs_pct(ma: float | None) -> float | None:
        if ma is None or ma == 0:
            return None
        return round((curr_close - ma) / ma * 100, 4)

    def _above(ma: float | None) -> int | None:
        return int(curr_close > ma) if ma is not None else None

    def _gt(a: float | None, b: float | None) -> int | None:
        if a is None or b is None:
            return None
        return int(a > b)

    return {
        "close_price": round(curr_close, 4),
        "volume": curr_volume,
        "rsi": _r(rsi),
        "adx": _r(adx),
        "ema20": _r(ema20),
        "ema50": _r(ema50),
        "ema150": _r(ema150),
        "ema200": _r(ema200),
        "sma20": _r(sma20),
        "sma50": _r(sma50),
        "sma150": _r(sma150),
        "sma200": _r(sma200),
        "price_vs_ema20_pct": _vs_pct(ema20),
        "price_vs_ema50_pct": _vs_pct(ema50),
        "price_vs_ema150_pct": _vs_pct(ema150),
        "price_vs_ema200_pct": _vs_pct(ema200),
        "price_vs_sma20_pct": _vs_pct(sma20),
        "price_vs_sma50_pct": _vs_pct(sma50),
        "price_vs_sma150_pct": _vs_pct(sma150),
        "price_vs_sma200_pct": _vs_pct(sma200),
        "price_above_ema20": _above(ema20),
        "price_above_ema50": _above(ema50),
        "price_above_ema150": _above(ema150),
        "price_above_ema200": _above(ema200),
        "price_above_sma20": _above(sma20),
        "price_above_sma50": _above(sma50),
        "price_above_sma150": _above(sma150),
        "price_above_sma200": _above(sma200),
        "ema20_above_ema50": _gt(ema20, ema50),
        "ema50_above_ema150": _gt(ema50, ema150),
        "ema50_above_ema200": _gt(ema50, ema200),
        "ema150_above_ema200": _gt(ema150, ema200),
        "sma20_above_sma50": _gt(sma20, sma50),
        "sma50_above_sma150": _gt(sma50, sma150),
        "sma50_above_sma200": _gt(sma50, sma200),
        "sma150_above_sma200": _gt(sma150, sma200),
        "bb_upper": _r(bb_upper),
        "bb_middle": _r(bb_middle),
        "bb_lower": _r(bb_lower),
        "bb_pct_b": _r(bb_pct_b),
        "bb_width_pct": _r(bb_width_pct),
        "price_above_bb_middle": price_above_bb_middle,
        "price_above_bb_upper": price_above_bb_upper,
        "price_below_bb_lower": price_below_bb_lower,
        "rv20": _r(rv20),
        "atr_pct": _r(atr_pct),
        "volume_ratio": _r(volume_ratio),
        "roc20": _r(roc20),
        "macd_histogram": _r(macd_histogram),
        "pct_from_52wk_high": _r(pct_from_52wk_high),
        "sector": sector,
    }


def _last(series: pd.Series | None) -> float | None:
    if series is None or series.empty:
        return None
    val = series.iloc[-1]
    return float(val) if not pd.isna(val) else None


def _r(val: float | None, d: int = 4) -> float | None:
    return round(val, d) if val is not None else None
