"""Tests for run_signal_walk_forward in src/algo_detective/signal_backtest.py
— verifies IS/OOS fold generation and degradation-ratio reporting over a
pooled signal event set, reusing the backtester's existing fold logic.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.signal_backtest import run_signal_walk_forward


def _make_ohlcv(periods: int = 400) -> pd.DataFrame:
    closes = [100.0] * periods
    dates = pd.date_range(start="2024-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": closes, "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes], "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


@pytest.fixture(autouse=True)
def _patch_data_sources():
    fixture = _make_ohlcv()
    with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
         patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
        mock_hist.side_effect = lambda symbol, **kwargs: fixture.copy()
        mock_opts.return_value = {}
        yield


def _events_across_dates(dates: list[str], ticker: str = "AAPL") -> list[dict]:
    return [{"date": d, "ticker": ticker, "is_prime": 1} for d in dates]


class TestRunSignalWalkForward:
    def test_returns_no_folds_when_events_empty(self):
        result = run_signal_walk_forward({"adr20_pct_max": 4.0}, events=[])
        assert result["folds"] == []

    def test_generates_at_least_one_fold_with_enough_signal_dates(self):
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=300)]
        events = _events_across_dates(dates)
        result = run_signal_walk_forward(
            {"adr20_pct_max": 4.0}, in_sample_days=200, out_of_sample_days=50, events=events,
        )
        assert len(result["folds"]) >= 1
        fold = result["folds"][0]
        assert set(fold) == {
            "fold_number", "is_start", "is_end", "oos_start", "oos_end",
            "is_stats", "oos_stats", "degradation",
        }

    def test_no_folds_when_not_enough_signal_dates_for_one_fold(self):
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=10)]
        events = _events_across_dates(dates)
        result = run_signal_walk_forward(
            {"adr20_pct_max": 4.0}, in_sample_days=200, out_of_sample_days=50, events=events,
        )
        assert result["folds"] == []

    def test_generates_at_least_one_fold_with_default_day_counts(self):
        """Regression test: run_signal_walk_forward's own defaults (not an
        override) must produce at least one fold for a realistic count of
        distinct signal-firing dates. Before the fix, the defaults
        (in_sample_days=756, out_of_sample_days=252) were copied from the
        daily-trading-bar domain (src/backtester/models.py) and silently
        produced zero folds for any plausible signal-date count, since a
        moderately selective gate fires on only tens to a few hundred
        distinct dates over its labeled history — nowhere near the combined
        1008 dates the old defaults required."""
        dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range("2024-01-02", periods=150)]
        events = _events_across_dates(dates)
        result = run_signal_walk_forward({"adr20_pct_max": 4.0}, events=events)
        assert len(result["folds"]) >= 1
