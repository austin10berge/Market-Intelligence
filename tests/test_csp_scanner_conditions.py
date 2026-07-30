"""Unit tests for CSP scanner technical condition filter logic.

Tests cover:
  - _check_conditions: condition types, None-indicator handling,
    stacking (AND semantics), unknown-condition fail-safe
  - _compute_technical_indicators: returns correct keys, handles short history
  - apply_technical_conditions: pass-through, max_rsi gate behavior
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.screener.csp_scanner import (
    ScannerParams,
    _check_conditions,
    _compute_technical_indicators,
    apply_technical_conditions,
)

_INDICATORS_PATCH = "src.screener.csp_scanner._compute_technical_indicators"

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_indicators(
    *,
    price: float | None = 100.0,
    sma20: float | None = 95.0,
    sma50: float | None = 90.0,
    sma200: float | None = 80.0,
    bb_pct_from_lower: float | None = 1.0,
    rsi: float | None = 37.0,
    adx: float | None = 25.0,
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
        "adx": adx,
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
    def test_passes_price_below_midline(self):
        # price=94, sma20=95 → price < midline
        ind = _make_indicators(price=94.0, sma20=95.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is True

    def test_passes_price_below_lower_band(self):
        # price well below midline (even below lower band) still passes
        ind = _make_indicators(price=80.0, sma20=95.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is True

    def test_fails_price_above_midline(self):
        # price=100, sma20=95 → price > midline
        ind = _make_indicators(price=100.0, sma20=95.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False

    def test_fails_price_equal_midline(self):
        # price == sma20 is not strictly below
        ind = _make_indicators(price=95.0, sma20=95.0)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False

    def test_fails_when_sma20_none(self):
        ind = _make_indicators(sma20=None)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False

    def test_fails_when_price_none(self):
        ind = _make_indicators(price=None)
        ok, res = _check_conditions(ind, ["price_near_lower_bb"])
        assert res["price_near_lower_bb"] is False


class TestMaxRsiGate:
    """max_rsi is applied as an always-on gate in apply_technical_conditions."""

    def test_no_rsi_filter_when_max_rsi_100(self):
        rows = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        tickers, _ = apply_technical_conditions(
            rows, ScannerParams(max_rsi=100.0, min_adx=0.0, max_adx=100.0)
        )
        assert tickers == ["AAPL", "MSFT"]

    def test_rsi_gate_passes_ticker_below_threshold(self):
        from unittest.mock import MagicMock, patch
        fake_indicators = {"price": 100.0, "sma20": 105.0, "sma50": 100.0, "sma200": 90.0,
                           "bb_lower": 95.0, "bb_pct_from_lower": 5.0, "rsi": 38.0}
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_indicators):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(max_rsi=45.0, min_adx=0.0, max_adx=100.0)
            )
        assert "TEST" in tickers

    def test_rsi_gate_blocks_ticker_above_threshold(self):
        from unittest.mock import MagicMock, patch
        fake_indicators = {"price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
                           "bb_lower": 85.0, "bb_pct_from_lower": 15.0, "rsi": 62.0}
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_indicators):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(max_rsi=50.0, min_adx=0.0, max_adx=100.0)
            )
        assert "TEST" not in tickers

    def test_rsi_gate_fails_at_exact_threshold(self):
        """RSI must be strictly less than max_rsi — equal does not pass."""
        from unittest.mock import MagicMock, patch
        fake_indicators = {"price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
                           "bb_lower": 85.0, "bb_pct_from_lower": 15.0, "rsi": 50.0}
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_indicators):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(max_rsi=50.0, min_adx=0.0, max_adx=100.0)
            )
        assert "TEST" not in tickers

    def test_rsi_gate_fails_when_rsi_none(self):
        """When RSI cannot be computed the gate blocks the ticker."""
        from unittest.mock import MagicMock, patch
        fake_indicators = {"price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
                           "bb_lower": 85.0, "bb_pct_from_lower": 15.0, "rsi": None}
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_indicators):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(max_rsi=50.0, min_adx=0.0, max_adx=100.0)
            )
        assert "TEST" not in tickers


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
        expected_keys = {"price", "sma20", "sma50", "sma200", "bb_lower", "bb_pct_from_lower", "rsi", "adx"}
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

    def test_contains_adx_key(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert "adx" in result

    def test_adx_is_float_for_sufficient_history(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert isinstance(result["adx"], float)

    def test_adx_in_valid_range(self):
        """ADX is always 0–100."""
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["adx"] is not None
        assert 0.0 <= result["adx"] <= 100.0


def test_compute_technical_indicators_includes_return_5d():
    dates = pd.bdate_range(end="2024-06-28", periods=210)
    closes = [100.0] * 204 + [101.0, 102.0, 103.0, 104.0, 105.0, 110.0]
    hist = pd.DataFrame(
        {
            "Open": closes, "High": closes, "Low": closes, "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )

    indicators = _compute_technical_indicators("TEST", hist)

    assert indicators is not None
    assert "return_5d" in indicators
    expected = round((110.0 - 101.0) / 101.0 * 100, 2)
    assert indicators["return_5d"] == pytest.approx(expected, abs=0.05)


# ── apply_technical_conditions: pass-through when no conditions ───────────────

class TestApplyTechnicalConditions:
    def test_no_conditions_passes_all_through(self):
        rows = [
            {"symbol": "AAPL"},
            {"symbol": "MSFT"},
            {"symbol": "GOOG"},
        ]
        tickers, out_rows = apply_technical_conditions(
            rows, ScannerParams(max_rsi=100.0, min_adx=0.0, max_adx=100.0)
        )
        assert tickers == ["AAPL", "MSFT", "GOOG"]
        assert len(out_rows) == 3
        # Each row should have an empty technical_conditions dict
        for row in out_rows:
            assert row.get("technical_conditions") == {}


class TestScannerParamsAdx:
    def test_default_min_adx(self):
        assert ScannerParams().min_adx == 15.0

    def test_default_max_adx(self):
        assert ScannerParams().max_adx == 50.0

    def test_from_query_uses_defaults_when_none(self):
        p = ScannerParams.from_query()
        assert p.min_adx == 15.0
        assert p.max_adx == 50.0

    def test_from_query_accepts_min_adx(self):
        p = ScannerParams.from_query(min_adx=20.0)
        assert p.min_adx == 20.0

    def test_from_query_accepts_max_adx(self):
        p = ScannerParams.from_query(max_adx=40.0)
        assert p.max_adx == 40.0

    def test_cache_key_changes_with_adx_params(self):
        p1 = ScannerParams(min_adx=15.0, max_adx=50.0)
        p2 = ScannerParams(min_adx=20.0, max_adx=50.0)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()


class TestAdxGate:
    """ADX min/max gate in apply_technical_conditions."""

    def _fake_indicators_with_adx(self, adx: float | None) -> dict:
        return {
            "price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
            "bb_lower": 85.0, "bb_pct_from_lower": 15.0, "rsi": 40.0,
            "adx": adx,
        }

    def test_adx_gate_inactive_at_zero_to_100(self):
        """min_adx=0, max_adx=100 never filters anything — pass-through."""
        rows = [{"symbol": "AAPL"}, {"symbol": "MSFT"}]
        tickers, _ = apply_technical_conditions(
            rows, ScannerParams(max_rsi=100.0, min_adx=0.0, max_adx=100.0)
        )
        assert tickers == ["AAPL", "MSFT"]

    def test_adx_gate_passes_ticker_in_range(self):
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(25.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" in tickers

    def test_adx_gate_blocks_ticker_below_min(self):
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(10.0)  # below min_adx=15
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" not in tickers

    def test_adx_gate_blocks_ticker_above_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(60.0)  # above max_adx=50
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" not in tickers

    def test_adx_gate_passes_at_exact_min(self):
        """Boundary: ADX == min_adx should pass (inclusive)."""
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(15.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" in tickers

    def test_adx_gate_passes_at_exact_max(self):
        """Boundary: ADX == max_adx should pass (inclusive)."""
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(50.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" in tickers

    def test_adx_gate_blocks_when_adx_none(self):
        """If ADX cannot be computed, the ticker is dropped when filter is active."""
        from unittest.mock import MagicMock, patch
        fake_ind = self._fake_indicators_with_adx(None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(min_adx=15.0, max_adx=50.0))
        assert "TEST" not in tickers


class TestScannerParamsNewFields:
    def test_defaults_are_set(self):
        p = ScannerParams()
        assert p.min_fcf_b == 0.0
        assert p.max_debt_to_equity == 2.0
        assert p.min_revenue_growth == pytest.approx(-0.10)
        assert p.min_earnings_growth is None
        assert p.min_dividend_yield is None

    def test_from_query_maps_new_params(self):
        p = ScannerParams.from_query(
            min_fcf_b=5.0,
            max_debt_to_equity=1.5,
            min_revenue_growth=0.05,
            min_earnings_growth=-0.10,
            min_dividend_yield=0.02,
        )
        assert p.min_fcf_b == 5.0
        assert p.max_debt_to_equity == 1.5
        assert p.min_revenue_growth == 0.05
        assert p.min_earnings_growth == pytest.approx(-0.10)
        assert p.min_dividend_yield == 0.02

    def test_from_query_null_disables_gate(self):
        p = ScannerParams.from_query(
            min_fcf_b=None,
            max_debt_to_equity=None,
            min_revenue_growth=None,
        )
        assert p.min_fcf_b is None
        assert p.max_debt_to_equity is None
        assert p.min_revenue_growth is None

    def test_cache_key_differs_with_new_params(self):
        p1 = ScannerParams()
        p2 = ScannerParams(min_fcf_b=10.0)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()


# ── ScannerParams: new technical gates (rv20, bb_width, volume_ratio, ────────
# pct_from_52wk_high, adr20_pct, price_vs_ema200_pct) ──────────────────────────

class TestScannerParamsTechnicalGates:
    def test_defaults_are_none(self):
        p = ScannerParams()
        assert p.rv20_max is None
        assert p.bb_width_pct_min is None
        assert p.bb_width_pct_max is None
        assert p.volume_ratio_max is None
        assert p.pct_from_52wk_high_max is None
        assert p.adr20_pct_max is None
        assert p.price_vs_ema200_pct_min is None

    def test_from_query_maps_all_gates(self):
        p = ScannerParams.from_query(
            rv20_max=45.0,
            bb_width_pct_min=4.0,
            bb_width_pct_max=21.0,
            volume_ratio_max=1.15,
            pct_from_52wk_high_max=12.0,
            adr20_pct_max=4.0,
            price_vs_ema200_pct_min=5.0,
        )
        assert p.rv20_max == 45.0
        assert p.bb_width_pct_min == 4.0
        assert p.bb_width_pct_max == 21.0
        assert p.volume_ratio_max == 1.15
        assert p.pct_from_52wk_high_max == 12.0
        assert p.adr20_pct_max == 4.0
        assert p.price_vs_ema200_pct_min == 5.0

    def test_from_query_defaults_to_none_when_omitted(self):
        p = ScannerParams.from_query()
        assert p.rv20_max is None
        assert p.adr20_pct_max is None
        assert p.price_vs_ema200_pct_min is None

    def test_cache_key_differs_with_adr20_pct_max(self):
        p1 = ScannerParams()
        p2 = ScannerParams(adr20_pct_max=4.0)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()


# ── _compute_technical_indicators: new fields ──────────────────────────────────

class TestComputeTechnicalIndicatorsNewFields:
    def test_adr20_pct_exact_for_constant_daily_range(self):
        """_make_hist sets High=close*1.01, Low=close*0.98 on every bar, so the
        daily range is exactly 3% of close every day — adr20_pct must be 3.0."""
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["adr20_pct"] == pytest.approx(3.0, abs=0.01)

    def test_adr20_pct_none_for_short_history(self):
        hist = _make_hist(n=15)
        result = _compute_technical_indicators("TEST", hist)
        assert result is None  # below the 50-bar minimum for any result

    def test_volume_ratio_exact_for_constant_volume(self):
        """_make_hist uses a constant Volume, so today's volume always equals
        the 20-day average — volume_ratio must be exactly 1.0."""
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["volume_ratio"] == pytest.approx(1.0, abs=1e-6)

    def test_pct_from_52wk_high_is_non_negative_when_present(self):
        hist = _make_hist(n=260)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["pct_from_52wk_high"] is not None
        assert result["pct_from_52wk_high"] >= 0.0

    def test_pct_from_52wk_high_none_below_252_bars(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["pct_from_52wk_high"] is None

    def test_bb_width_pct_positive_for_sufficient_history(self):
        hist = _make_hist(n=250)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["bb_width_pct"] is not None
        assert result["bb_width_pct"] > 0.0

    def test_price_vs_ema200_pct_positive_for_strong_uptrend(self):
        hist = _make_hist(n=400, start=50.0, trend=0.5)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["price_vs_ema200_pct"] is not None
        assert result["price_vs_ema200_pct"] > 0.0

    def test_price_vs_ema200_pct_none_below_200_bars(self):
        hist = _make_hist(n=150)
        result = _compute_technical_indicators("TEST", hist)
        assert result is not None
        assert result["price_vs_ema200_pct"] is None


# ── apply_technical_conditions: new numeric gates ──────────────────────────────
# Each gate is tested with RSI/ADX neutralized (max_rsi=100, min_adx=0,
# max_adx=100) so only the gate under test can affect the outcome.

def _fake_full_indicators(**overrides) -> dict:
    """Indicators dict with every gate-relevant field at a neutral/passing
    value, overridden per test to exercise one specific gate."""
    base = {
        "price": 100.0, "sma20": 95.0, "sma50": 90.0, "sma200": 80.0,
        "bb_lower": 85.0, "bb_pct_from_lower": 15.0,
        "rsi": 40.0, "adx": 25.0,
        "bb_width_pct": 10.0,
        "volume_ratio": 1.0,
        "pct_from_52wk_high": 5.0,
        "adr20_pct": 2.0,
        "price_vs_ema200_pct": 10.0,
    }
    base.update(overrides)
    return base


_NEUTRAL = dict(max_rsi=100.0, min_adx=0.0, max_adx=100.0)


class TestRv20MaxGate:
    """rv20_max reads row['rv20'] (set upstream by apply_vol_filter), not the
    _compute_technical_indicators dict — checked before indicators are computed."""

    def test_passes_below_max(self):
        from unittest.mock import MagicMock, patch
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=_fake_full_indicators()):
            rows = [{"symbol": "TEST", "rv20": 30.0}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(**_NEUTRAL, rv20_max=45.0))
        assert "TEST" in tickers

    def test_blocks_above_max(self):
        rows = [{"symbol": "TEST", "rv20": 60.0}]
        tickers, _ = apply_technical_conditions(rows, ScannerParams(**_NEUTRAL, rv20_max=45.0))
        assert "TEST" not in tickers

    def test_excluded_row_has_consistent_shape(self):
        """A row excluded by rv20_max must still get technical_conditions set,
        like every other exclusion path in apply_technical_conditions."""
        rows = [{"symbol": "TEST", "rv20": 60.0}]
        apply_technical_conditions(rows, ScannerParams(**_NEUTRAL, rv20_max=45.0))
        assert rows[0]["technical_conditions"] == {}

    def test_boundary_at_exact_max_passes(self):
        from unittest.mock import MagicMock, patch
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=_fake_full_indicators()):
            rows = [{"symbol": "TEST", "rv20": 45.0}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(**_NEUTRAL, rv20_max=45.0))
        assert "TEST" in tickers

    def test_null_rv20_passes(self):
        from unittest.mock import MagicMock, patch
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=_fake_full_indicators()):
            rows = [{"symbol": "TEST", "rv20": None}]
            tickers, _ = apply_technical_conditions(rows, ScannerParams(**_NEUTRAL, rv20_max=45.0))
        assert "TEST" in tickers


class TestBbWidthPctGates:
    def test_min_passes_above_floor(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=10.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_min=4.0)
            )
        assert "TEST" in tickers

    def test_min_blocks_below_floor(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=2.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_min=4.0)
            )
        assert "TEST" not in tickers

    def test_min_null_fails(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_min=4.0)
            )
        assert "TEST" not in tickers

    def test_max_passes_below_ceiling(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=15.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_max=21.0)
            )
        assert "TEST" in tickers

    def test_max_blocks_above_ceiling(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=25.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_max=21.0)
            )
        assert "TEST" not in tickers

    def test_max_null_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(bb_width_pct=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, bb_width_pct_max=21.0)
            )
        assert "TEST" in tickers


class TestVolumeRatioMaxGate:
    def test_passes_below_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(volume_ratio=0.9)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, volume_ratio_max=1.15)
            )
        assert "TEST" in tickers

    def test_blocks_above_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(volume_ratio=1.5)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, volume_ratio_max=1.15)
            )
        assert "TEST" not in tickers

    def test_null_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(volume_ratio=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, volume_ratio_max=1.15)
            )
        assert "TEST" in tickers


class TestPctFrom52wkHighMaxGate:
    def test_passes_below_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(pct_from_52wk_high=8.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, pct_from_52wk_high_max=12.0)
            )
        assert "TEST" in tickers

    def test_blocks_above_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(pct_from_52wk_high=20.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, pct_from_52wk_high_max=12.0)
            )
        assert "TEST" not in tickers

    def test_null_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(pct_from_52wk_high=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, pct_from_52wk_high_max=12.0)
            )
        assert "TEST" in tickers


class TestAdr20PctMaxGate:
    def test_passes_below_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=2.5)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, adr20_pct_max=4.0)
            )
        assert "TEST" in tickers

    def test_blocks_above_max(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=6.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, adr20_pct_max=4.0)
            )
        assert "TEST" not in tickers

    def test_boundary_at_exact_max_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=4.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, adr20_pct_max=4.0)
            )
        assert "TEST" in tickers

    def test_null_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, adr20_pct_max=4.0)
            )
        assert "TEST" in tickers


class TestPriceVsEma200PctMinGate:
    def test_passes_above_min(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(price_vs_ema200_pct=10.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, price_vs_ema200_pct_min=5.0)
            )
        assert "TEST" in tickers

    def test_blocks_below_min(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(price_vs_ema200_pct=1.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, price_vs_ema200_pct_min=5.0)
            )
        assert "TEST" not in tickers

    def test_boundary_at_exact_min_passes(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(price_vs_ema200_pct=5.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, price_vs_ema200_pct_min=5.0)
            )
        assert "TEST" in tickers

    def test_null_fails(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(price_vs_ema200_pct=None)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows, ScannerParams(**_NEUTRAL, price_vs_ema200_pct_min=5.0)
            )
        assert "TEST" not in tickers


class TestMultipleNumericGatesStack:
    """Numeric gates combine with AND semantics — one failure blocks even if
    all others pass."""

    def test_one_gate_failing_blocks_despite_others_passing(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=2.0, volume_ratio=99.0)  # volume_ratio way over
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows,
                ScannerParams(**_NEUTRAL, adr20_pct_max=4.0, volume_ratio_max=1.15),
            )
        assert "TEST" not in tickers

    def test_all_gates_passing_lets_ticker_through(self):
        from unittest.mock import MagicMock, patch
        fake_ind = _fake_full_indicators(adr20_pct=2.0, volume_ratio=1.0)
        with patch("src.screener.csp_scanner.get_ohlcv", return_value=MagicMock(empty=False)), \
             patch(_INDICATORS_PATCH, return_value=fake_ind):
            rows = [{"symbol": "TEST"}]
            tickers, _ = apply_technical_conditions(
                rows,
                ScannerParams(**_NEUTRAL, adr20_pct_max=4.0, volume_ratio_max=1.15),
            )
        assert "TEST" in tickers
