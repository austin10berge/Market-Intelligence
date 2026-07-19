"""Tests for the CLI report printers and argument plumbing in
src/algo_detective/signal_backtest.py."""
from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.signal_backtest import (
    main,
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


class TestCLIArgPlumbing:
    def test_in_sample_and_out_of_sample_flags_reach_run_signal_walk_forward(self, monkeypatch):
        argv = [
            "signal_backtest.py",
            "--criteria", '{"adr20_pct_max": 4.0}',
            "--mode", "walk-forward",
            "--in-sample-days", "45",
            "--out-of-sample-days", "15",
        ]
        monkeypatch.setattr("sys.argv", argv)
        with patch(
            "src.algo_detective.signal_backtest.run_signal_walk_forward"
        ) as mock_wf, patch(
            "src.algo_detective.signal_backtest.print_signal_walk_forward_report"
        ):
            mock_wf.return_value = {"criteria": {}, "folds": []}
            main()
        _, kwargs = mock_wf.call_args
        assert kwargs["in_sample_days"] == 45
        assert kwargs["out_of_sample_days"] == 15

    def test_in_sample_and_out_of_sample_flags_default_to_60_and_20(self, monkeypatch):
        argv = [
            "signal_backtest.py",
            "--criteria", '{"adr20_pct_max": 4.0}',
            "--mode", "walk-forward",
        ]
        monkeypatch.setattr("sys.argv", argv)
        with patch(
            "src.algo_detective.signal_backtest.run_signal_walk_forward"
        ) as mock_wf, patch(
            "src.algo_detective.signal_backtest.print_signal_walk_forward_report"
        ):
            mock_wf.return_value = {"criteria": {}, "folds": []}
            main()
        _, kwargs = mock_wf.call_args
        assert kwargs["in_sample_days"] == 60
        assert kwargs["out_of_sample_days"] == 20
