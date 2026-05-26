"""End-to-end tests for the backtest engine.

These exercise:
  * Long stock entry/exit with stop_loss and take_profit
  * Calendar-day option DTE accounting
  * Long-call P&L sign + premium accounting
  * Short-put (CSP) P&L sign + premium-credited-on-entry accounting
  * Pyramiding scale-in modes (pullback-only / signal-only / both) and reference modes
  * Campaign tagging
  * Strategy serialization with legacy `scale_in_trigger` / `scale_in_value` keys
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.backtester.engine import run_backtest
from src.backtester.models import (
    BacktestRequest,
    ExitStrategy,
    OptionPosition,
    OptionType,
    OptionsConfig,
    PositionSizing,
    PositionSizingMethod,
    PyramidingConfig,
    PyramidingReference,
    PyramidingTriggerMode,
    StrategyDefinition,
)
from src.backtester.options import snap_strike


def _make_df(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """Build an OHLCV frame; OHLC are derived from close with a small spread."""
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.005 for c in closes],
            "Low": [c * 0.995 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )
    return df


def _always_true_entry() -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {
                "type": "threshold",
                "indicator": {"name": "CLOSE", "params": {}},
                "comparator": "gt",
                "value": 0.0,
            }
        ],
    }


def _never_entry() -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {
                "type": "threshold",
                "indicator": {"name": "CLOSE", "params": {}},
                "comparator": "lt",
                "value": 0.0,
            }
        ],
    }


# ── Stock long path ───────────────────────────────────────────────────────────


def test_stock_long_take_profit_fills_at_limit():
    closes = [100, 105, 108, 112, 120, 115]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="tp",
        entry=_always_true_entry(),
        exit=ExitStrategy(take_profit_pct=10.0),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=10),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    first = res.trades[0]
    assert first.exit_reason == "take_profit"
    # Took profit at 10% over the $100 entry → exit price of 110
    assert first.exit_price == pytest.approx(110.0)
    # 10 shares × $10 gain = $100
    assert first.pnl == pytest.approx(100.0)


def test_stock_long_stop_loss_fills_at_limit():
    closes = [100, 98, 95, 89, 85]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="sl",
        entry=_always_true_entry(),
        exit=ExitStrategy(stop_loss_pct=10.0),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=10),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    first = res.trades[0]
    assert first.exit_reason == "stop_loss"
    assert first.exit_price == pytest.approx(90.0)
    assert first.pnl == pytest.approx(-100.0)


def test_equity_curve_carries_avg_cost_and_position_count():
    closes = [100, 100, 100, 100]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="avg",
        entry=_always_true_entry(),
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=5),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    # Every equity-curve point should expose the dashboard's overlay keys
    for pt in res.equity_curve[:-1]:  # last bar may close everything
        assert "avg_cost" in pt
        assert "n_positions" in pt
    # When holding, avg_cost equals the single fill price ($100)
    assert res.equity_curve[1]["avg_cost"] == pytest.approx(100.0)
    assert res.equity_curve[1]["n_positions"] == 1


# ── Options: DTE in calendar days ─────────────────────────────────────────────


def test_option_dte_uses_calendar_days_not_trading_bars():
    # 30 calendar days of business bars ≈ 22 trading bars.
    # The OLD engine would let the option live ~30 trading bars (~42 cal days);
    # the new engine should expire on the bar whose date is ≥ 30 cal days past entry.
    closes = [100] * 30  # 30 business days
    df = _make_df(closes, start="2024-04-01")
    strategy = StrategyDefinition(
        name="dte",
        entry=_always_true_entry(),
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_DOLLAR, value=500),
        options=OptionsConfig(
            enabled=True, type=OptionType.CALL, position=OptionPosition.LONG,
            target_dte=30, target_delta=0.50,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    # The option opens on bar 0 (2024-04-01) and must expire on or before 2024-05-01
    assert len(res.trades) >= 1
    t = res.trades[0]
    assert t.exit_reason == "expiration"
    entry = pd.Timestamp(t.entry_date)
    exit_ = pd.Timestamp(t.exit_date)
    elapsed_days = (exit_ - entry).days
    # Must be ≤ 30 cal days, not ~42
    assert elapsed_days <= 30, f"option lived {elapsed_days} cal days, expected ≤ 30"
    assert elapsed_days >= 25, "option should not expire well before its DTE either"


# ── Options: short put (CSP) accounting ──────────────────────────────────────


def test_trade_records_underlying_prices_for_position_chart():
    """The Position-chart panel needs the underlying price at entry/exit even
    for option trades, where entry_price/exit_price are premiums."""
    closes = [100, 102, 104, 106, 108, 110]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="underlying",
        entry=_always_true_entry(),
        exit=ExitStrategy(take_profit_pct=20.0),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=1),
        options=OptionsConfig(enabled=True, type=OptionType.CALL, position=OptionPosition.LONG,
                              target_dte=60, target_delta=0.5),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    assert len(res.trades) >= 1
    t = res.trades[0]
    assert t.is_option
    assert t.entry_underlying_price == pytest.approx(100.0)
    # Exit underlying should be the closing price on the exit bar (some later bar)
    assert t.exit_underlying_price > 100.0
    # The premium fields are NOT the underlying for options
    assert t.entry_price != t.entry_underlying_price


def test_short_put_credits_premium_on_entry_and_profits_when_premium_decays():
    # Flat-to-rising underlying → short put profits from theta + delta.
    closes = [100, 100, 101, 102, 103, 104, 105]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="csp",
        entry=_always_true_entry(),
        exit=ExitStrategy(take_profit_pct=50.0),  # buy back when premium halves
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=1),
        options=OptionsConfig(
            enabled=True,
            type=OptionType.PUT,
            position=OptionPosition.SHORT,
            target_dte=30,
            target_delta=0.30,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    initial_cash = req.initial_capital
    res = run_backtest(req, df)
    # We should have at least one closed trade
    assert len(res.trades) >= 1
    t = res.trades[0]
    assert t.direction == "short"
    assert t.option_position == "short"
    assert t.option_type == "put"
    # P&L positive when we bought back cheaper than we sold
    assert t.pnl > 0, f"short put should profit on rising underlying, got pnl={t.pnl}"
    # Equity after the trade should be > initial (premium kept)
    final_equity = res.equity_curve[-1]["equity"]
    assert final_equity > initial_cash


# ── Pyramiding ────────────────────────────────────────────────────────────────


def test_pullback_only_scale_in_uses_last_fill_reference():
    # Step the price down 10% at a time. With pullback_pct=10 / last_fill ref,
    # we should fill on every 10% drop, up to max_positions.
    closes = [100.0, 100.0, 90.0, 90.0, 81.0, 81.0, 72.9, 72.9, 65.6]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="pullback",
        entry=_always_true_entry(),  # fires every bar; first bar opens the campaign
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=10),
        pyramiding=PyramidingConfig(
            enabled=True,
            max_positions=4,
            trigger_mode=PyramidingTriggerMode.PULLBACK_ONLY,
            pullback_pct=10.0,
            pullback_reference=PyramidingReference.LAST_FILL,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=50_000)
    res = run_backtest(req, df)
    # The final equity curve carries n_positions; we should have hit 4 tranches
    peak_positions = max(pt["n_positions"] for pt in res.equity_curve)
    assert peak_positions == 4
    # All four tranches should share the same campaign_id (only closed at end_of_data)
    assert {t.campaign_id for t in res.trades} == {1}


def test_signal_only_scale_in_respects_entry_tree():
    # Entry signal = "Close < 95". So even though pullback would fit, no scale-in
    # happens while close ≥ 95.
    closes = [100, 100, 90, 90, 90, 90, 90, 90, 90, 90]
    df = _make_df(closes)
    entry_tree = {
        "operator": "AND",
        "conditions": [{
            "type": "threshold",
            "indicator": {"name": "CLOSE", "params": {}},
            "comparator": "lt",
            "value": 95.0,
        }],
    }
    strategy = StrategyDefinition(
        name="sig",
        entry=entry_tree,
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=5),
        pyramiding=PyramidingConfig(
            enabled=True,
            max_positions=3,
            trigger_mode=PyramidingTriggerMode.SIGNAL_ONLY,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    # Bar 0 closes at 100 → entry blocked; bar 2 closes at 90 → opens.
    # Then bars 3+ also satisfy the signal → scale-in keeps firing until max_positions=3.
    peak_positions = max(pt["n_positions"] for pt in res.equity_curve)
    assert peak_positions == 3


def test_both_mode_requires_pullback_and_signal():
    # Signal = close < 95. Pullback = 10% from last fill.
    # The first entry opens at the first bar where close < 95.
    # Subsequent scale-ins need ANOTHER 10% drop AND close still < 95.
    closes = [100, 100, 90, 90, 90, 80, 80, 80, 71, 71]
    df = _make_df(closes)
    entry_tree = {
        "operator": "AND",
        "conditions": [{
            "type": "threshold",
            "indicator": {"name": "CLOSE", "params": {}},
            "comparator": "lt",
            "value": 95.0,
        }],
    }
    strategy = StrategyDefinition(
        name="both",
        entry=entry_tree,
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=5),
        pyramiding=PyramidingConfig(
            enabled=True,
            max_positions=5,
            trigger_mode=PyramidingTriggerMode.BOTH,
            pullback_pct=10.0,
            pullback_reference=PyramidingReference.LAST_FILL,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    peak_positions = max(pt["n_positions"] for pt in res.equity_curve)
    # 1st fill @ 90, then drop to 80 (≥10% from 90) → 2nd fill, then 71 → 3rd. Total 3.
    assert peak_positions == 3


def test_original_entry_reference_measures_drop_from_first_fill():
    # First fill at 100. With ref=ORIGINAL_ENTRY and pullback_pct=10, scale-in
    # fires whenever close ≤ 90. With LAST_FILL it would require ≤ 81 for the
    # third tranche, but here ORIGINAL means tranches 2/3/4 all fire at ≤ 90.
    closes = [100, 90, 90, 90, 90]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="orig",
        entry=_always_true_entry(),
        exit=ExitStrategy(),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=5),
        pyramiding=PyramidingConfig(
            enabled=True,
            max_positions=4,
            trigger_mode=PyramidingTriggerMode.PULLBACK_ONLY,
            pullback_pct=10.0,
            pullback_reference=PyramidingReference.ORIGINAL_ENTRY,
        ),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    assert max(pt["n_positions"] for pt in res.equity_curve) == 4


# ── Campaigns ─────────────────────────────────────────────────────────────────


def test_campaign_id_resets_after_full_exit():
    closes = [100, 110, 105, 95, 105, 115]
    df = _make_df(closes)
    strategy = StrategyDefinition(
        name="camp",
        entry=_always_true_entry(),
        exit=ExitStrategy(take_profit_pct=5.0),
        position_sizing=PositionSizing(method=PositionSizingMethod.FIXED_SHARES, value=1),
    )
    req = BacktestRequest(strategy=strategy, ticker="TEST", initial_capital=10_000)
    res = run_backtest(req, df)
    ids = sorted({t.campaign_id for t in res.trades})
    assert ids == list(range(1, len(ids) + 1)), f"campaign ids should be sequential, got {ids}"
    assert len(res.trades) >= 2


# ── Legacy strategy serialization ────────────────────────────────────────────


def test_legacy_scale_in_trigger_field_is_migrated():
    """Strategies saved by the old UI used scale_in_trigger/scale_in_value.

    The new model must transparently accept those keys and translate them so
    older rows in the `backtest_strategies` table still work.
    """
    cfg = PyramidingConfig(
        enabled=True,
        max_positions=3,
        scale_in_trigger="pullback",
        scale_in_value=12.0,
    )
    assert cfg.trigger_mode == PyramidingTriggerMode.PULLBACK_ONLY
    assert cfg.pullback_pct == 12.0


# ── Helpers ───────────────────────────────────────────────────────────────────


def test_snap_strike_uses_realistic_grid():
    assert snap_strike(11.83, 11.83) == 12.0          # $1 grid below $25
    assert snap_strike(127.6, 127.6) == 127.5          # $2.50 grid between $25 and $200
    assert snap_strike(442.3, 442.3) == 440.0          # $5 grid above $200


def test_indicator_cache_avoids_recomputation():
    """Two conditions referencing the same RSI(14) should compute the series once.

    We verify by counting calls into compute_indicator while iterating the same df.
    """
    from src.backtester import conditions as cond_module
    from src.backtester.conditions import evaluate_condition_tree

    df = _make_df([100 + i for i in range(50)])

    call_counter = {"n": 0}
    original_compute = cond_module.compute_indicator

    def counting_compute(name, df_, params):
        call_counter["n"] += 1
        return original_compute(name, df_, params)

    cond_module.compute_indicator = counting_compute
    try:
        tree = {
            "operator": "OR",
            "conditions": [
                {"type": "threshold", "indicator": {"name": "RSI", "params": {"period": 14}}, "comparator": "lt", "value": 70},
                {"type": "threshold", "indicator": {"name": "RSI", "params": {"period": 14}}, "comparator": "gt", "value": 30},
            ],
        }
        # Evaluate at every bar — should hit the cache after the first compute.
        for i in range(len(df)):
            evaluate_condition_tree(tree, df, i)
    finally:
        cond_module.compute_indicator = original_compute

    # 50 bars × 2 conditions = 100 evaluations; cache hit means only ONE compute.
    assert call_counter["n"] == 1, f"expected 1 indicator compute, got {call_counter['n']}"
