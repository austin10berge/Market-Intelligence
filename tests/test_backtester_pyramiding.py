"""Tests for the pyramiding trigger modes in the backtest engine.

Covers the trigger_mode ("pullback_only" / "entry_signal" / "both") and
pullback_reference ("entry" / "rolling") behavior in src/backtester/engine.py,
which previously had zero test coverage.
"""
from __future__ import annotations

import pandas as pd
import pytest

from src.backtester.engine import run_backtest
from src.backtester.models import (
    BacktestRequest,
    PyramidingConfig,
    StrategyDefinition,
)


def _make_df(closes: list[float], start: str = "2024-01-02") -> pd.DataFrame:
    """Build an OHLCV frame; High/Low are derived from close with a small spread."""
    dates = pd.date_range(start=start, periods=len(closes), freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.005 for c in closes],
            "Low": [c * 0.995 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * len(closes),
        },
        index=dates,
    )


def _threshold(comparator: str, value: float) -> dict:
    return {
        "operator": "AND",
        "conditions": [
            {
                "type": "threshold",
                "indicator": {"name": "CLOSE", "params": {}},
                "comparator": comparator,
                "value": value,
            }
        ],
    }


def _always_true_entry() -> dict:
    return _threshold("gt", 0.0)


def _run(closes: list[float], entry: dict, pyramiding: PyramidingConfig) -> list:
    strategy = StrategyDefinition(entry=entry, pyramiding=pyramiding)
    request = BacktestRequest(strategy=strategy, ticker="TEST")
    result = run_backtest(request, _make_df(closes))
    return result.trades


class TestPullbackOnlyTriggerMode:
    def test_scales_in_only_after_price_drop_from_entry(self):
        closes = [100, 100, 100, 80, 80]
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="pullback_only", pullback_pct=15.0)
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 2

    def test_no_scale_in_when_price_never_drops_enough(self):
        closes = [100, 100, 100, 95, 95]
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="pullback_only", pullback_pct=15.0)
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 1

    def test_ignores_entry_signal_once_pullback_satisfied(self):
        """Pure pullback scale-in should fire even when the entry tree would
        evaluate False, since is_pullback_scalein skips the entry-tree check."""
        closes = [100, 100, 100, 80, 80]
        never_after_drop = _threshold("gt", 90.0)  # false once price drops to 80
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="pullback_only", pullback_pct=15.0)
        trades = _run(closes, never_after_drop, pyr)
        assert len(trades) == 2


class TestEntrySignalTriggerMode:
    def test_scales_in_on_repeated_signal_regardless_of_price(self):
        closes = [100, 100, 100]
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="entry_signal")
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 2

    def test_stops_scaling_in_once_signal_turns_false(self):
        closes = [100, 100, 60, 60]
        signal_false_below_80 = _threshold("gt", 80.0)  # true at 100, false at 60
        pyr = PyramidingConfig(enabled=True, max_positions=3, trigger_mode="entry_signal")
        trades = _run(closes, signal_false_below_80, pyr)
        assert len(trades) == 2  # entry at bar0, one scale-in at bar1, none after signal drops


class TestBothTriggerMode:
    def test_blocks_scale_in_when_signal_false_even_if_pullback_satisfied(self):
        closes = [100, 100, 100, 60, 60]
        signal_false_after_drop = _threshold("gt", 80.0)  # true at 100, false at 60
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="both", pullback_pct=15.0)
        trades = _run(closes, signal_false_after_drop, pyr)
        assert len(trades) == 1

    def test_scales_in_when_both_pullback_and_signal_true(self):
        closes = [100, 100, 100, 80, 80]
        pyr = PyramidingConfig(enabled=True, max_positions=2, trigger_mode="both", pullback_pct=15.0)
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 2


class TestPullbackReference:
    def test_entry_reference_measures_drop_from_original_entry(self):
        """With pullback_reference='entry', a price that rises then partially
        retraces (but stays above the original entry) should not scale in."""
        closes = [100, 130, 130, 110, 110]
        pyr = PyramidingConfig(
            enabled=True, max_positions=2, trigger_mode="pullback_only",
            pullback_pct=15.0, pullback_reference="entry",
        )
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 1

    def test_rolling_reference_measures_drop_from_high_since_entry(self):
        """With pullback_reference='rolling', the same price path scales in
        because the retrace is measured from the interim high, not the
        original entry price."""
        closes = [100, 130, 130, 110, 110]
        pyr = PyramidingConfig(
            enabled=True, max_positions=2, trigger_mode="pullback_only",
            pullback_pct=15.0, pullback_reference="rolling",
        )
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 2


class TestLegacyFieldBackwardCompat:
    """PyramidingConfig renamed scale_in_trigger/scale_in_value to
    trigger_mode/pullback_pct. Strategies saved before that rename (in
    backtest_strategies or a direct API caller) still use the old names —
    Pydantic's default extra="ignore" would otherwise silently drop them."""

    def test_scale_in_trigger_pullback_maps_to_pullback_only(self):
        pyr = PyramidingConfig(scale_in_trigger="pullback", scale_in_value=15.0)
        assert pyr.trigger_mode == "pullback_only"
        assert pyr.pullback_pct == 15.0

    def test_scale_in_trigger_entry_signal_maps_to_entry_signal(self):
        pyr = PyramidingConfig(scale_in_trigger="entry_signal")
        assert pyr.trigger_mode == "entry_signal"

    def test_new_field_names_take_precedence_over_legacy_ones(self):
        pyr = PyramidingConfig(
            trigger_mode="both", pullback_pct=20.0,
            scale_in_trigger="pullback", scale_in_value=5.0,
        )
        assert pyr.trigger_mode == "both"
        assert pyr.pullback_pct == 20.0

    def test_legacy_fields_absent_keeps_defaults(self):
        pyr = PyramidingConfig()
        assert pyr.trigger_mode == "entry_signal"
        assert pyr.pullback_pct is None

    def test_legacy_mapping_runs_end_to_end_in_backtest(self):
        """A strategy saved with the old field names should still scale in
        on a pullback, not silently fall back to entry_signal-only behavior."""
        closes = [100, 100, 100, 80, 80]
        pyr = PyramidingConfig(enabled=True, max_positions=2, scale_in_trigger="pullback", scale_in_value=15.0)
        trades = _run(closes, _always_true_entry(), pyr)
        assert len(trades) == 2
