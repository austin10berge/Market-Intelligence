"""Unit tests for CSP scanner technical condition filter logic.

Tests cover:
  - _check_conditions: all 8 condition types, None-indicator handling,
    stacking (AND semantics), unknown-condition fail-safe
  - _compute_technical_indicators: returns correct keys, handles short history
  - apply_technical_conditions: passes all through when conditions=[]
"""

from __future__ import annotations

import math
import pandas as pd
import numpy as np
import pytest

from src.screener.csp_scanner import (
    _check_conditions,
    _compute_technical_indicators,
    apply_technical_conditions,
    ScannerParams,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_indicators(
    *,
    price: float | None = 100.0,
    sma20: float | None = 95.0,
    sma50: float | None = 90.0,
    sma200: float | None = 80.0,
    bb_pct_from_lower: float | None = 1.0,
    rsi: float | None = 37.0,
) -> dict:
    """Return a minimal indicators dict with controllable fields."""
    return {
        "price": price,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "bb_lower": None,
        "bb_pct_from_lower": bb_pct_from_lower,
        "rsi": rsi,
    }


def _make_price_series(n: int = 250, start: float = 100.0, trend: float = 0.1) -> pd.Series:
    """Generate a simple upward-trending close price series."""
    prices = [start + trend * i + np.random.default_rng(42).normal(0, 0.5) for i in range(n)]
    return pd.Series(prices, dtype=float)


def _make_hist(n: int = 250, start: float = 100.0, trend: float = 0.1) -> pd.DataFrame:
    """Build a minimal OHLCV DataFrame with a tz-aware DatetimeIndex.

    Mirrors the format returned by yfinance: business-day DatetimeIndex
    with US/Eastern timezone, which is what _compute_technical_indicators
    expects (it calls hist.index.tz_convert(None)).
    """
    close = _make_price_series(n, start, trend)
    # Build a business-day date range ending today, tz-aware like yfinance
    start = pd.Timestamp("2020-01-02", tz="America/New_York")
    dates = pd.bdate_range(start=start, periods=n, tz="America/New_York")
    return pd.DataFrame(
        {
            "Open":   (close * 0.99).values,
            "High":   (close * 1.01).values,
            "Low":    (close * 0.98).values,
            "Close":  close.values,
            "Volume": 1_000_000,
        },
        index=dates,
    )


# ── _check_conditions: individual condition correctness ───────────────────────

class TestCheckConditionsSma50AboveSma200:
    def test_passes_when_sma50_gt_sma200(self):
        ind = _make_indicators(sma50=150.0, sma200=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is True
        assert ok is True

    def test_fails_when_sma50_lt_sma200(self):
        ind = _make_indicators(sma50=90.0, sma200=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is False
        assert ok is False

    def test_fails_when_equal(self):
        ind = _make_indicators(sma50=100.0, sma200=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is False

    def test_fails_when_sma200_is_none(self):
        """Regression: old code passed when sma200=None (else True). Must now fail."""
        ind = _make_indicators(sma50=150.0, sma200=None)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is False
        assert ok is False

    def test_fails_when_sma50_is_none(self):
        ind = _make_indicators(sma50=None, sma200=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is False

    def test_fails_when_both_smas_are_none(self):
        ind = _make_indicators(sma50=None, sma200=None)
        ok, res = _check_conditions(ind, ["sma50_above_sma200"])
        assert res["sma50_above_sma200"] is False


class TestCheckConditionsSma20AboveSma50:
    def test_passes_when_sma20_gt_sma50(self):
        ind = _make_indicators(sma20=110.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["sma20_above_sma50"])
        assert res["sma20_above_sma50"] is True
        assert ok is True

    def test_fails_when_sma20_lt_sma50(self):
        ind = _make_indicators(sma20=90.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["sma20_above_sma50"])
        assert res["sma20_above_sma50"] is False

    def test_fails_when_none(self):
        ind = _make_indicators(sma20=None, sma50=100.0)
        ok, res = _check_conditions(ind, ["sma20_above_sma50"])
        assert res["sma20_above_sma50"] is False


class TestCheckConditionsSma50AboveSma20:
    def test_passes_when_sma50_gt_sma20(self):
        ind = _make_indicators(sma50=110.0, sma20=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma20"])
        assert res["sma50_above_sma20"] is True

    def test_fails_when_sma50_lt_sma20(self):
        ind = _make_indicators(sma50=90.0, sma20=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma20"])
        assert res["sma50_above_sma20"] is False

    def test_fails_when_none(self):
        ind = _make_indicators(sma50=None, sma20=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma20"])
        assert res["sma50_above_sma20"] is False


class TestCheckConditionsPriceVsMa:
    def test_price_above_sma50_passes(self):
        ind = _make_indicators(price=110.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["price_above_sma50"])
        assert res["price_above_sma50"] is True

    def test_price_above_sma50_fails(self):
        ind = _make_indicators(price=90.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["price_above_sma50"])
        assert res["price_above_sma50"] is False

    def test_price_above_sma50_fails_when_sma50_none(self):
        """Regression: used to pass silently."""
        ind = _make_indicators(price=110.0, sma50=None)
        ok, res = _check_conditions(ind, ["price_above_sma50"])
        assert res["price_above_sma50"] is False

    def test_price_above_sma200_passes(self):
        ind = _make_indicators(price=150.0, sma200=100.0)
        ok, res = _check_conditions(ind, ["price_above_sma200"])
        assert res["price_above_sma200"] is True

    def test_price_above_sma200_fails_when_sma200_none(self):
        """Regression: used to pass silently."""
        ind = _make_indicators(price=150.0, sma200=None)
        ok, res = _check_conditions(ind, ["price_above_sma200"])
        assert res["price_above_sma200"] is False

    def test_price_below_sma50_passes(self):
        ind = _make_indicators(price=90.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["price_below_sma50"])
        assert res["price_below_sma50"] is True

    def test_price_below_sma50_fails(self):
        ind = _make_indicators(price=110.0, sma50=100.0)
        ok, res = _check_conditions(ind, ["price_below_sma50"])
        assert res["price_below_sma50"] is False

    def test_price_below_sma50_fails_when_sma50_none(self):
        ind = _make_indicators(price=90.0, sma50=None)
        ok, res = _check_conditions(ind, ["price_below_sma50"])
        assert res["price_below_sma50"] is False


class TestCheckConditionsBollingerBand:
    def test_passes_within_2pct(self):
        ind = _make_indicators(bb_pct_from_lower=1.5)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is True

    def test_passes_at_exactly_0pct(self):
        ind = _make_indicators(bb_pct_from_lower=0.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is True

    def test_passes_at_exactly_2pct(self):
        ind = _make_indicators(bb_pct_from_lower=2.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is True

    def test_fails_above_2pct(self):
        ind = _make_indicators(bb_pct_from_lower=3.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False

    def test_fails_when_none(self):
        ind = _make_indicators(bb_pct_from_lower=None)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False

    def test_fails_below_lower_band(self):
        """Negative pct means price is below the lower band — should fail."""
        ind = _make_indicators(bb_pct_from_lower=-0.5)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False


class TestCheckConditionsRsiOversoldBounce:
    def test_passes_in_range_30_45(self):
        for rsi_val in [30.0, 37.5, 45.0]:
            ind = _make_indicators(rsi=rsi_val)
            ok, res = _check_conditions(ind, ["rsi_oversold_bounce"])
            assert res["rsi_oversold_bounce"] is True, f"Expected pass for RSI={rsi_val}"

    def test_fails_below_30(self):
        ind = _make_indicators(rsi=29.9)
        ok, res = _check_conditions(ind, ["rsi_oversold_bounce"])
        assert res["rsi_oversold_bounce"] is False

    def test_fails_above_45(self):
        ind = _make_indicators(rsi=45.1)
        ok, res = _check_conditions(ind, ["rsi_oversold_bounce"])
        assert res["rsi_oversold_bounce"] is False

    def test_fails_when_none(self):
        ind = _make_indicators(rsi=None)
        ok, res = _check_conditions(ind, ["rsi_oversold_bounce"])
        assert res["rsi_oversold_bounce"] is False


# ── _check_conditions: stacking (AND semantics) ────────────────────────────────

class TestCheckConditionsStacking:
    def test_all_pass_returns_true(self):
        # price=110 > sma50=100, sma50=100 > sma200=80
        ind = _make_indicators(price=110.0, sma50=100.0, sma200=80.0)
        ok, res = _check_conditions(ind, ["price_above_sma50", "sma50_above_sma200"])
        assert ok is True
        assert res["price_above_sma50"] is True
        assert res["sma50_above_sma200"] is True

    def test_one_fails_returns_false(self):
        # price=90 < sma50=100 (fails), sma50=100 > sma200=80 (passes)
        ind = _make_indicators(price=90.0, sma50=100.0, sma200=80.0)
        ok, res = _check_conditions(ind, ["price_above_sma50", "sma50_above_sma200"])
        assert ok is False
        assert res["price_above_sma50"] is False
        assert res["sma50_above_sma200"] is True

    def test_empty_conditions_returns_true(self):
        ind = _make_indicators()
        ok, res = _check_conditions(ind, [])
        assert ok is True
        assert res == {}

    def test_three_conditions_all_pass(self):
        ind = _make_indicators(price=110.0, sma20=105.0, sma50=100.0, sma200=80.0)
        conditions = ["price_above_sma50", "sma20_above_sma50", "sma50_above_sma200"]
        ok, res = _check_conditions(ind, conditions)
        assert ok is True

    def test_three_conditions_middle_fails(self):
        # sma20=95 < sma50=100 → sma20_above_sma50 fails
        ind = _make_indicators(price=110.0, sma20=95.0, sma50=100.0, sma200=80.0)
        conditions = ["price_above_sma50", "sma20_above_sma50", "sma50_above_sma200"]
        ok, res = _check_conditions(ind, conditions)
        assert ok is False
        assert res["sma20_above_sma50"] is False


# ── _check_conditions: unknown condition fail-safe ────────────────────────────

class TestCheckConditionsUnknown:
    def test_unknown_condition_fails(self):
        ind = _make_indicators()
        ok, res = _check_conditions(ind, ["totally_made_up_condition"])
        assert res["totally_made_up_condition"] is False
        assert ok is False

    def test_unknown_condition_blocks_valid_conditions(self):
        ind = _make_indicators(sma50=150.0, sma200=100.0)
        ok, res = _check_conditions(ind, ["sma50_above_sma200", "unknown_cond"])
        assert res["sma50_above_sma200"] is True
        assert res["unknown_cond"] is False
        assert ok is False


# ── _compute_technical_indicators ─────────────────────────────────────────────

class TestComputeTechnicalIndicators:
    def test_returns_none_for_short_history(self):
        hist = _make_hist(n=30)
        result = _compute_technical_indicators("TEST", hist)
        assert result is None

    def test_returns_none_for_empty_df(self):
        hist = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        result = _compute_technical_indicators("TEST", hist)
        assert result is None

    def test_returns_dict_for_sufficient_history(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert isinstance(result, dict)

    def test_contains_expected_keys(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        expected_keys = {"price", "sma20", "sma50", "sma200", "bb_lower", "bb_pct_from_lower", "rsi"}
        assert expected_keys.issubset(result.keys())

    def test_sma200_is_none_for_short_history(self):
        """Tickers with only 100 bars should have sma200=None."""
        hist = _make_hist(n=100)
        result = _compute_technical_indicators("TEST", hist)
        # hist has 100 bars (>= 50 required minimum) so we get a result but sma200=None
        assert result is not None
        assert result["sma200"] is None

    def test_sma200_is_present_for_long_history(self):
        hist = _make_hist(n=400)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["sma200"] is not None
        assert math.isfinite(result["sma200"])

    def test_price_matches_last_close(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["price"] == pytest.approx(float(hist["Close"].iloc[-1]), abs=0.01)

    def test_uptrend_sma_ordering(self):
        """For a strong uptrend: sma20 > sma50 > sma200."""
        hist = _make_hist(n=400, start=50.0, trend=0.5)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["sma20"] > result["sma50"] > result["sma200"]


# ── apply_technical_conditions: pass-through when no conditions ───────────────

class TestApplyTechnicalConditions:
    def test_no_conditions_passes_all_through(self):
        rows = [
            {"symbol": "AAPL"},
            {"symbol": "MSFT"},
            {"symbol": "GOOG"},
        ]
        tickers, out_rows = apply_technical_conditions(rows, conditions=[])
        assert tickers == ["AAPL", "MSFT", "GOOG"]
        assert len(out_rows) == 3
        # Each row should have an empty technical_conditions dict
        for row in out_rows:
            assert row.get("technical_conditions") == {}
