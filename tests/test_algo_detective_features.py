from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.algo_detective.features import compute_features


def _make_ohlcv(n: int = 250, trend: str = "up") -> pd.DataFrame:
    """Build synthetic OHLCV with a clean uptrend."""
    np.random.seed(42)
    dates = pd.bdate_range("2024-01-02", periods=n)
    base = 100.0
    closes = []
    for i in range(n):
        noise = np.random.normal(0, 0.5)
        drift = 0.05 if trend == "up" else -0.05
        base = base + drift + noise
        closes.append(max(base, 1.0))
    closes = np.array(closes)
    highs = closes * 1.005
    lows = closes * 0.995
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    volumes = np.random.randint(500_000, 2_000_000, size=n)
    return pd.DataFrame(
        {"Open": opens, "High": highs, "Low": lows, "Close": closes, "Volume": volumes},
        index=dates,
    )


def test_returns_none_for_insufficient_history():
    df = _make_ohlcv(n=100)
    result = compute_features("GE", "2024-06-01", df)
    assert result is None


def test_returns_dict_for_sufficient_history():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result is not None
    assert isinstance(result, dict)


def test_required_keys_present():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    required = [
        "rsi", "adx", "ema20", "ema50", "ema150", "ema200",
        "sma20", "sma50", "sma150", "sma200",
        "price_vs_ema50_pct", "price_above_ema50",
        "ema20_above_ema50", "ema50_above_ema200",
        "bb_pct_b", "bb_width_pct", "price_above_bb_middle",
        "rv20", "atr_pct", "volume_ratio", "roc20", "macd_histogram",
        "pct_from_52wk_high", "close_price", "volume", "sector",
    ]
    for key in required:
        assert key in result, f"Missing key: {key}"


def test_uptrend_booleans_are_set():
    df = _make_ohlcv(n=250, trend="up")
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["price_above_ema50"] == 1
    assert result["price_above_ema200"] == 1
    assert result["ema20_above_ema50"] == 1


def test_rsi_in_valid_range():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["rsi"] is not None
    assert 0 <= result["rsi"] <= 100


def test_rv20_is_annualized():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["rv20"] is not None
    assert 0.0 < result["rv20"] < 5.0  # annualized, not raw daily


def test_bb_pct_b_range():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df)
    assert result["bb_pct_b"] is not None
    # Can exceed 0-1 if price breaks outside bands, but a steady trend stays inside
    assert -1.0 <= result["bb_pct_b"] <= 2.0


def test_sector_passes_through():
    df = _make_ohlcv(n=250)
    as_of = df.index[-1].strftime("%Y-%m-%d")
    result = compute_features("GE", as_of, df, sector="Industrials")
    assert result["sector"] == "Industrials"


def test_no_lookahead():
    df = _make_ohlcv(n=250, trend="up")
    # Use a date 20 bars before the end — result should differ from using the full df
    cutoff_date = df.index[-20].strftime("%Y-%m-%d")
    result_early = compute_features("GE", cutoff_date, df)
    result_late = compute_features("GE", df.index[-1].strftime("%Y-%m-%d"), df)
    assert result_early is not None
    assert result_late is not None
    # RSI should differ because different data windows
    # (won't always differ by much but close_price must differ)
    assert result_early["close_price"] != result_late["close_price"]
