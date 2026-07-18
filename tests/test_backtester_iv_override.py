"""Tests for the iv_override column hook in _open_position
(src/backtester/engine.py) — lets a caller-supplied real IV (e.g.
algo_detective's joined detective_options.best_iv) take precedence
over the recomputed realized-vol proxy at entry.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.models import BacktestRequest, Direction, OptionsConfig, StrategyDefinition


def _entry_when_close_above(threshold: float) -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {"type": "threshold", "indicator": {"name": "CLOSE", "params": {}}, "comparator": "gt", "value": threshold},
        ],
    }


def _options_strategy(entry: dict) -> StrategyDefinition:
    return StrategyDefinition(
        entry=entry,
        direction=Direction.SHORT,
        options=OptionsConfig(enabled=True, type="put", target_delta=0.25, target_dte=5),
    )


class TestIvOverride:
    def test_uses_iv_override_at_entry_when_present(self):
        closes = [100.0] * 25
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [0.80] + [None] * 24,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(0.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.80

    def test_falls_back_to_default_when_neither_override_nor_rv20_present(self):
        """Entry at bar 0, before the rv20 rolling window has filled in
        (needs 20 bars) -> falls back to the pre-existing 0.30 default,
        same as before this change."""
        closes = [100.0] * 25
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [None] * 25,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(0.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.30

    def test_prefers_iv_override_over_computed_rv20_when_both_present(self):
        """Entry after bar 20 so rv20 is a real (non-NaN) number driven by
        a sharp price jump -> a small override (0.05, floored to 0.10)
        must still win over whatever large value rv20 computed to."""
        closes = [100.0] * 20 + [200.0] * 5
        dates = pd.date_range("2024-01-02", periods=25, freq="B")
        df = pd.DataFrame(
            {
                "Open": closes, "High": [c * 1.01 for c in closes],
                "Low": [c * 0.99 for c in closes], "Close": closes,
                "Volume": [1_000_000] * 25,
                "iv_override": [None] * 20 + [0.05] * 5,
            },
            index=dates,
        )
        request = BacktestRequest(strategy=_options_strategy(_entry_when_close_above(150.0)), ticker="TEST")
        result = run_backtest(request, df)
        assert result.trades[0].option_iv_entry == 0.10  # 0.05 floored, not rv20's much larger value
