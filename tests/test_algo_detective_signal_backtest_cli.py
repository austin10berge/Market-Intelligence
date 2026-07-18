"""Tests for the CLI report printers in src/algo_detective/signal_backtest.py."""
from __future__ import annotations

from src.algo_detective.signal_backtest import (
    print_signal_backtest_report,
    print_signal_walk_forward_report,
)


class TestReportPrinters:
    def test_print_signal_backtest_report_smoke(self, capsys):
        result = {
            "criteria": {"adr20_pct_max": 4.0},
            "stats": {
                "total_trades": 10, "wins": 6, "losses": 4, "win_rate_pct": 60.0,
                "profit_factor": 1.8, "avg_pnl": 42.0, "avg_pnl_pct": 12.0, "total_pnl": 420.0,
            },
            "trades": [],
            "tickers_skipped": ["ZZZZ"],
        }
        print_signal_backtest_report(result)
        captured = capsys.readouterr()
        assert "Trades simulated : 10" in captured.out
        assert "ZZZZ" in captured.out

    def test_print_signal_walk_forward_report_smoke(self, capsys):
        result = {
            "criteria": {"adr20_pct_max": 4.0},
            "folds": [{
                "fold_number": 1, "is_start": "2024-01-02", "is_end": "2024-06-01",
                "oos_start": "2024-06-02", "oos_end": "2024-12-01",
                "is_stats": {"win_rate_pct": 60.0, "profit_factor": 1.8},
                "oos_stats": {"win_rate_pct": 55.0, "profit_factor": 1.5},
                "degradation": {"win_rate_pct": 0.917},
            }],
        }
        print_signal_walk_forward_report(result)
        captured = capsys.readouterr()
        assert "Fold 1" in captured.out
