"""Tests for src/algo_detective/signal_backtest.py — simulates real CSP
wheel trades on every historical gate hit and pools trade-level P&L.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.signal_backtest import compute_pooled_trade_stats, run_signal_backtest


def _trade(pnl: float, pnl_pct: float) -> dict:
    return {"pnl": pnl, "pnl_pct": pnl_pct}


class TestComputePooledTradeStats:
    def test_empty_trades_returns_zeroed_stats(self):
        stats = compute_pooled_trade_stats([])
        assert stats["total_trades"] == 0
        assert stats["profit_factor"] is None

    def test_computes_win_rate_and_pnl_totals(self):
        trades = [_trade(100.0, 10.0), _trade(-50.0, -5.0), _trade(200.0, 20.0)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["total_trades"] == 3
        assert stats["wins"] == 2
        assert stats["losses"] == 1
        assert stats["win_rate_pct"] == round(2 / 3 * 100.0, 2)
        assert stats["total_pnl"] == 250.0
        assert stats["avg_pnl"] == round(250.0 / 3, 2)

    def test_profit_factor_is_gross_profit_over_gross_loss(self):
        trades = [_trade(100.0, 10.0), _trade(-25.0, -2.5)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["profit_factor"] == 4.0

    def test_profit_factor_none_when_no_losses(self):
        trades = [_trade(100.0, 10.0)]
        stats = compute_pooled_trade_stats(trades)
        assert stats["profit_factor"] is None


def _make_ohlcv(periods: int = 30) -> pd.DataFrame:
    closes = [100.0] * periods
    dates = pd.date_range(start="2026-01-02", periods=periods, freq="B")
    return pd.DataFrame(
        {
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low": [c * 0.99 for c in closes],
            "Close": closes,
            "Volume": [1_000_000] * periods,
        },
        index=dates,
    )


@pytest.fixture
def _patched_data_sources():
    fixture = _make_ohlcv()
    with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
         patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
        mock_hist.side_effect = lambda symbol, **kwargs: fixture.copy()
        mock_opts.return_value = {}
        yield


class TestRunSignalBacktest:
    def test_produces_one_pooled_trade_per_ticker_with_a_signal(self, _patched_data_sources):
        events = [
            {"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1},
            {"date": "2026-01-02", "ticker": "MSFT", "is_prime": 0},
        ]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert result["stats"]["total_trades"] == 2
        assert result["tickers_skipped"] == []

    def test_trades_are_short_put_positions(self, _patched_data_sources):
        events = [{"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1}]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        trade = result["trades"][0]
        assert trade.direction == "short"
        assert trade.is_option is True
        assert trade.option_type == "put"

    def test_stats_dict_has_expected_keys(self, _patched_data_sources):
        events = [{"date": "2026-01-02", "ticker": "AAPL", "is_prime": 1}]
        result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert set(result["stats"]) == {
            "total_trades", "wins", "losses", "win_rate_pct",
            "profit_factor", "avg_pnl", "avg_pnl_pct", "total_pnl",
        }

    def test_skips_ticker_with_no_available_price_data(self):
        events = [{"date": "2026-01-02", "ticker": "UNKNOWN", "is_prime": 1}]
        with patch("src.algo_detective.signal_backtest.get_historical_data") as mock_hist, \
             patch("src.algo_detective.signal_backtest.get_options_index") as mock_opts:
            mock_hist.return_value = pd.DataFrame()
            mock_opts.return_value = {}
            result = run_signal_backtest({"adr20_pct_max": 4.0}, events=events)
        assert result["tickers_skipped"] == ["UNKNOWN"]
        assert result["stats"]["total_trades"] == 0
