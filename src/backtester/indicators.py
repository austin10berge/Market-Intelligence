"""Technical indicator calculations on pandas DataFrames.

All functions accept a DataFrame with OHLCV columns (Open, High, Low, Close, Volume)
and return a Series or DataFrame aligned to the input index. No lookahead — each value
depends only on data at or before that index position.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int = 20) -> pd.Series:
    """Simple Moving Average."""
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, period: int = 20) -> pd.Series:
    """Exponential Moving Average."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing).

    Returns values 0–100. NaN for the first `period` bars.
    """
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    # Wilder's smoothing (equivalent to EMA with alpha = 1/period)
    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    result = 100.0 - (100.0 / (1.0 + rs))
    return result


def macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    """MACD — returns DataFrame with columns: macd_line, signal_line, histogram."""
    ema_fast = ema(series, fast)
    ema_slow = ema(series, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    }, index=series.index)


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    std_dev: float = 2.0,
) -> pd.DataFrame:
    """Bollinger Bands — returns DataFrame with columns: upper, middle, lower."""
    middle = sma(series, period)
    rolling_std = series.rolling(window=period, min_periods=period).std(ddof=1)
    upper = middle + (rolling_std * std_dev)
    lower = middle - (rolling_std * std_dev)
    return pd.DataFrame({
        "upper": upper,
        "middle": middle,
        "lower": lower,
    }, index=series.index)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range.

    Requires DataFrame with High, Low, Close columns.
    """
    high = df["High"]
    low = df["Low"]
    prev_close = df["Close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Volume Weighted Average Price (daily approximation).

    Uses (High + Low + Close) / 3 as typical price, weighted by volume.
    This computes a cumulative VWAP from the start of the data.
    For intraday use, data should be pre-split by session.
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    cumulative_tp_vol = (typical_price * df["Volume"]).cumsum()
    cumulative_vol = df["Volume"].cumsum()
    return cumulative_tp_vol / cumulative_vol.replace(0, np.nan)


def stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> pd.DataFrame:
    """Stochastic Oscillator — returns DataFrame with columns: k, d.

    Requires DataFrame with High, Low, Close columns.
    """
    lowest_low = df["Low"].rolling(window=k_period, min_periods=k_period).min()
    highest_high = df["High"].rolling(window=k_period, min_periods=k_period).max()

    denom = highest_high - lowest_low
    k = 100.0 * (df["Close"] - lowest_low) / denom.replace(0, np.nan)
    d = sma(k, d_period)

    return pd.DataFrame({"k": k, "d": d}, index=df.index)


def roc(series: pd.Series, period: int = 10) -> pd.Series:
    """Rate of Change (percentage)."""
    prev = series.shift(period)
    return ((series - prev) / prev.replace(0, np.nan)) * 100.0


# ── Indicator registry ────────────────────────────────────────────────────────
# Maps indicator names (as used in condition definitions) to computation functions.
# Each entry: (function, required_columns, default_params)


def compute_indicator(
    name: str,
    df: pd.DataFrame,
    params: dict | None = None,
) -> pd.Series:
    """Compute a named indicator and return the result as a Series.

    This is the single entry point used by the condition evaluator.
    Supports all registered indicators and their sub-components.
    """
    params = params or {}
    name_upper = name.upper()

    # Price references
    if name_upper == "PRICE" or name_upper == "CLOSE":
        return df["Close"]
    if name_upper == "OPEN":
        return df["Open"]
    if name_upper == "HIGH":
        return df["High"]
    if name_upper == "LOW":
        return df["Low"]
    if name_upper == "VOLUME":
        return df["Volume"].astype(float)

    # Simple indicators on Close
    if name_upper == "RSI":
        return rsi(df["Close"], period=params.get("period", 14))
    if name_upper == "SMA":
        return sma(df["Close"], period=params.get("period", 20))
    if name_upper == "EMA":
        return ema(df["Close"], period=params.get("period", 20))
    if name_upper == "ROC":
        return roc(df["Close"], period=params.get("period", 10))

    # MACD components
    if name_upper in ("MACD_LINE", "MACD_SIGNAL", "MACD_HISTOGRAM", "MACD"):
        macd_df = macd(
            df["Close"],
            fast=params.get("fast", 12),
            slow=params.get("slow", 26),
            signal=params.get("signal", 9),
        )
        if name_upper == "MACD" or name_upper == "MACD_LINE":
            return macd_df["macd_line"]
        if name_upper == "MACD_SIGNAL":
            return macd_df["signal_line"]
        if name_upper == "MACD_HISTOGRAM":
            return macd_df["histogram"]

    # Bollinger Band components
    if name_upper in ("BB_UPPER", "BB_MIDDLE", "BB_LOWER"):
        bb_df = bollinger_bands(
            df["Close"],
            period=params.get("period", 20),
            std_dev=params.get("std_dev", 2.0),
        )
        if name_upper == "BB_UPPER":
            return bb_df["upper"]
        if name_upper == "BB_MIDDLE":
            return bb_df["middle"]
        if name_upper == "BB_LOWER":
            return bb_df["lower"]

    # ATR
    if name_upper == "ATR":
        return atr(df, period=params.get("period", 14))

    # VWAP
    if name_upper == "VWAP":
        return vwap(df)

    # Stochastic components
    if name_upper in ("STOCH_K", "STOCH_D"):
        stoch_df = stochastic(
            df,
            k_period=params.get("k_period", 14),
            d_period=params.get("d_period", 3),
        )
        return stoch_df["k"] if name_upper == "STOCH_K" else stoch_df["d"]

    raise ValueError(f"Unknown indicator: {name}")
