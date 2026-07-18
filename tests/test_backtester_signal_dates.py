"""Tests for the signal_dates condition leaf in src/backtester/conditions.py.

Lets a backtest open a position on exactly the historical dates a gate
criteria fired for a ticker, rather than re-evaluating indicator logic
live — see docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.conditions import evaluate_condition_tree


def _make_df(start: str = "2024-01-02", periods: int = 5) -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": [100.0] * periods,
            "High": [101.0] * periods,
            "Low": [99.0] * periods,
            "Close": [100.0] * periods,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


class TestSignalDatesCondition:
    def test_true_on_matching_date(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": ["2024-01-03"]}
        assert evaluate_condition_tree(tree, df, 1) is True  # bar_idx 1 == 2024-01-03

    def test_false_on_non_matching_date(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": ["2024-01-03"]}
        assert evaluate_condition_tree(tree, df, 0) is False  # bar_idx 0 == 2024-01-02

    def test_false_when_dates_list_empty(self):
        df = _make_df()
        tree = {"type": "signal_dates", "dates": []}
        assert evaluate_condition_tree(tree, df, 0) is False

    def test_matches_multiple_dates_across_bars(self):
        df = _make_df(periods=5)
        tree = {"type": "signal_dates", "dates": ["2024-01-02", "2024-01-04"]}
        results = [evaluate_condition_tree(tree, df, i) for i in range(5)]
        assert results == [True, False, True, False, False]
