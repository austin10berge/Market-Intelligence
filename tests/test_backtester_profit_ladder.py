"""Tests for the profit_ladder time-tiered take-profit exit in
src/backtester/engine.py, modeling GTPro's 30/50/75%-by-day BTC rule.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.

Uses plain (non-option) short equity positions so the ladder's own
tiering logic is tested independently of options pricing.
"""
from __future__ import annotations

import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.models import (
    BacktestRequest,
    Direction,
    ExitStrategy,
    ProfitLadderTier,
    StrategyDefinition,
)


def _make_df(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.02 for c in closes],
            "Low": [c * 0.98 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )


def _entry_on_first_bar() -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {"type": "threshold", "indicator": {"name": "CLOSE", "params": {}}, "comparator": "gt", "value": 0.0},
        ],
    }


LADDER = [
    ProfitLadderTier(max_days_held=2, take_profit_pct=30.0),
    ProfitLadderTier(max_days_held=4, take_profit_pct=50.0),
    ProfitLadderTier(max_days_held=5, take_profit_pct=75.0),
]


def _run(closes: list[float], exit_strategy: ExitStrategy) -> list:
    strategy = StrategyDefinition(entry=_entry_on_first_bar(), direction=Direction.SHORT, exit=exit_strategy)
    request = BacktestRequest(strategy=strategy, ticker="TEST")
    result = run_backtest(request, _make_df(closes))
    return result.trades


class TestProfitLadder:
    def test_exits_at_early_tier_when_price_crashes_immediately(self):
        """Short position: a sharp price drop right after entry should
        hit the day-2 tier's 30% target within 2 bars, not ride further."""
        closes = [100.0, 65.0, 65.0, 65.0, 65.0, 65.0]
        trades = _run(closes, ExitStrategy(profit_ladder=LADDER))
        assert len(trades) == 1
        assert trades[0].exit_reason == "take_profit"
        assert trades[0].bars_held <= 2

    def test_does_not_exit_before_any_tier_threshold_reached(self):
        """Price never drops enough for any tier -> rides to end of data."""
        closes = [100.0] * 6
        trades = _run(closes, ExitStrategy(profit_ladder=LADDER))
        assert trades[0].exit_reason == "end_of_data"

    def test_fill_price_uses_matched_tier_pct_not_flat_take_profit(self):
        """When both profit_ladder and a flat take_profit_pct are set,
        the ladder's tier percentage must govern the fill, not the flat one."""
        closes = [100.0, 65.0, 65.0, 65.0, 65.0, 65.0]
        trades = _run(closes, ExitStrategy(take_profit_pct=99.0, profit_ladder=LADDER))
        trade = trades[0]
        assert trade.exit_reason == "take_profit"
        expected_fill = round(trade.entry_price * (1 - 30.0 / 100.0), 4)
        assert trade.exit_price == expected_fill
