"""Tests for src/algo_detective/signal_backtest.py — simulates real CSP
wheel trades on every historical gate hit and pools trade-level P&L.
See docs/superpowers/specs/2026-07-18-algo-detective-signal-backtest-design.md.
"""
from __future__ import annotations

from src.algo_detective.signal_backtest import compute_pooled_trade_stats


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
