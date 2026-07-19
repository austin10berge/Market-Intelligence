"""Tests for src/algo_detective/control_sync.py — Step 7 of the nightly
pipeline (and reused by backfill Phase 2). Computes control-universe
(is_prime=0) features for a date, never overwriting a same-day is_prime=1
label written earlier by label_sync.py (Task 5).
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""
from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.control_sync import sync_control_universe


class TestSyncControlUniverse:
    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_excludes_todays_primes_from_control_tickers(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = {"AEO"}
        mock_control.return_value = ["SPY", "MSFT"]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "SPY"}, {"ticker": "MSFT"}]

        count = sync_control_universe("2026-02-02")

        mock_control.assert_called_once_with("2026-02-02", exclude={"AEO"})
        assert count == 2

    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_all_control_tickers_flagged_is_prime_zero(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = set()
        mock_control.return_value = ["SPY"]
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        sync_control_universe("2026-02-02")

        called_ticker_flags = mock_compute.call_args.args[1]
        assert called_ticker_flags == [("SPY", 0)]

    @patch("src.algo_detective.control_sync.compute_and_store_for_date")
    @patch("src.algo_detective.control_sync.get_computed_pairs")
    @patch("src.algo_detective.control_sync.get_control_tickers")
    @patch("src.algo_detective.control_sync._get_todays_primes")
    def test_returns_zero_when_nothing_computed(
        self, mock_primes, mock_control, mock_pairs, mock_compute,
    ):
        mock_primes.return_value = set()
        mock_control.return_value = []
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        assert sync_control_universe("2026-02-02") == 0
