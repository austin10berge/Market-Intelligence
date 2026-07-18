"""Tests for short (sell-to-open) option support in src/backtester/engine.py.

The engine's options path was previously long-only: direction=SHORT
ignored the entry tree entirely, and short option positions paid premium
at entry / booked long-side P&L. These tests pin the corrected behavior
needed to simulate cash-secured puts (see
docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md).
"""
from __future__ import annotations

import pandas as pd

from src.backtester.engine import run_backtest
from src.backtester.models import (
    BacktestRequest,
    Direction,
    ExitStrategy,
    OptionsConfig,
    ProfitLadderTier,
    StrategyDefinition,
)


def _make_df(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )


def _signal_entry(dates: list[str]) -> dict:
    return {"type": "signal_dates", "dates": dates}


def _threshold_entry(comparator: str, value: float) -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {"type": "threshold", "indicator": {"name": "CLOSE", "params": {}}, "comparator": comparator, "value": value},
        ],
    }


class TestShortEntryRespectsConditions:
    def test_short_equity_enters_only_on_signal_date(self):
        """Previously a pure-SHORT strategy entered at bar 0 regardless of
        its entry tree; it must enter only when the tree fires."""
        closes = [100.0, 100.0, 80.0, 80.0, 80.0]
        df = _make_df(closes)
        signal_date = df.index[2].strftime("%Y-%m-%d")
        strategy = StrategyDefinition(entry=_signal_entry([signal_date]), direction=Direction.SHORT)
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), df)
        assert len(result.trades) == 1
        assert result.trades[0].entry_date == signal_date

    def test_short_equity_no_entry_when_tree_never_fires(self):
        closes = [100.0, 100.0, 100.0]
        strategy = StrategyDefinition(entry=_signal_entry([]), direction=Direction.SHORT)
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), _make_df(closes))
        assert result.trades == []

    def test_both_direction_behavior_unchanged(self):
        """Direction.BOTH keeps its existing semantics: longs when the tree
        fires, and it never opens shorts (pre-existing elif guard)."""
        closes = [100.0, 80.0, 80.0]
        strategy = StrategyDefinition(entry=_threshold_entry("gt", 90.0), direction=Direction.BOTH)
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), _make_df(closes))
        assert all(t.direction == "long" for t in result.trades)


class TestShortOptionMechanics:
    def test_short_put_profits_from_time_decay(self):
        """Selling an OTM put on a flat underlying: premium decays to ~0 by
        expiration, so the seller's P&L must be positive."""
        closes = [100.0] * 10
        df = _make_df(closes)
        strategy = StrategyDefinition(
            entry=_signal_entry([df.index[0].strftime("%Y-%m-%d")]),
            direction=Direction.SHORT,
            options=OptionsConfig(enabled=True, type="put", target_delta=0.25, target_dte=5),
        )
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), df)
        assert len(result.trades) >= 1
        trade = result.trades[0]
        assert trade.direction == "short"
        assert trade.is_option is True
        assert trade.option_type == "put"
        assert trade.exit_reason == "expiration"
        assert trade.pnl > 0

    def test_long_call_decay_pnl_unchanged(self):
        """Regression: a bought call on a flat underlying decays — long-side
        P&L stays negative, proving the long branch is untouched."""
        closes = [100.0] * 10
        df = _make_df(closes)
        strategy = StrategyDefinition(
            entry=_signal_entry([df.index[0].strftime("%Y-%m-%d")]),
            direction=Direction.LONG,
            options=OptionsConfig(enabled=True, type="call", target_delta=0.50, target_dte=5),
        )
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), df)
        assert len(result.trades) >= 1
        assert result.trades[0].pnl < 0

    def test_short_put_cash_increases_at_entry(self):
        """Sell-to-open must credit premium: equity on the entry bar should
        not drop below initial capital the way a debit (bought) position's
        commission/cost accounting would imply for a worthless-decaying
        asset. Concretely: with zero commission, final equity after the
        premium fully decays must exceed initial capital."""
        closes = [100.0] * 10
        df = _make_df(closes)
        strategy = StrategyDefinition(
            entry=_signal_entry([df.index[0].strftime("%Y-%m-%d")]),
            direction=Direction.SHORT,
            options=OptionsConfig(enabled=True, type="put", target_delta=0.25, target_dte=5),
        )
        result = run_backtest(
            BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=100000.0), df
        )
        assert result.equity_curve[-1]["equity"] > 100000.0


class TestShortOptionProfitLadder:
    def test_ladder_take_profit_books_premium_capture_pct(self):
        """A short put whose premium collapses (huge gap up) must exit via
        the day-0-2 tier at exactly 30% premium captured: fill = 0.70 x
        entry, pnl_pct == 30.0, pnl > 0."""
        closes = [100.0, 100.0] + [500.0] * 8
        df = _make_df(closes)
        ladder = [
            ProfitLadderTier(max_days_held=2, take_profit_pct=30.0),
            ProfitLadderTier(max_days_held=4, take_profit_pct=50.0),
            ProfitLadderTier(max_days_held=5, take_profit_pct=75.0),
        ]
        strategy = StrategyDefinition(
            entry=_signal_entry([df.index[0].strftime("%Y-%m-%d")]),
            direction=Direction.SHORT,
            options=OptionsConfig(enabled=True, type="put", target_delta=0.25, target_dte=5),
            exit=ExitStrategy(profit_ladder=ladder),
        )
        result = run_backtest(BacktestRequest(strategy=strategy, ticker="TEST"), df)
        assert len(result.trades) == 1
        trade = result.trades[0]
        assert trade.exit_reason == "take_profit"
        assert trade.exit_price == round(trade.entry_price * 0.70, 4)
        assert trade.pnl > 0
        assert round(trade.pnl_pct, 1) == 30.0
