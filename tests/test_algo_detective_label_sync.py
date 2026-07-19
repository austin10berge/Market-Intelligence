"""Tests for src/algo_detective/label_sync.py — Step 6 of the nightly
pipeline. Discovers new mLabs recap posts, parses them into (ticker, date)
pairs, computes features, and upserts is_prime=1 rows.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.label_sync import sync_new_labels


class TestSyncNewLabels:
    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_prime_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_skips_already_processed_slugs(
        self,
        mock_known,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
    ):
        mock_known.return_value = {"results_boring_puts_2026_07_06"}
        mock_index.return_value = [
            "results_boring_puts_2026_07_06",
            "results_boring_puts_2026_07_13",
        ]
        mock_trades.return_value = [{"ticker": "NVO", "open_date": "2026-07-15"}]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "NVO", "date": "2026-07-15", "is_prime": 1}]

        sync_new_labels()

        mock_trades.assert_called_once_with("results_boring_puts_2026_07_13")

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_prime_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_groups_trades_by_date_and_computes_prime_flag(
        self,
        mock_known,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2026_02_02"]
        mock_trades.return_value = [
            {"ticker": "AEO", "open_date": "2026-02-02"},
            {"ticker": "UAL", "open_date": "2026-02-03"},
        ]
        mock_pairs.return_value = set()
        mock_compute.side_effect = lambda date, flags, pairs, ohlcv_fallback_fn=None: [
            {"ticker": t, "date": date, "is_prime": f} for t, f in flags
        ]

        count = sync_new_labels()

        calls = {c.args[0]: c.args[1] for c in mock_compute.call_args_list}
        assert calls == {
            "2026-02-02": [("AEO", 1)],
            "2026-02-03": [("UAL", 1)],
        }
        assert count == 2

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_prime_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_checkpoints_slug_with_trade_count_after_processing(
        self,
        mock_known,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2025_09_01"]
        mock_trades.return_value = []  # PDF-era post, no table
        mock_pairs.return_value = set()
        mock_compute.return_value = []

        sync_new_labels()

        mock_record.assert_called_once_with("results_boring_puts_2025_09_01", trades_found=0)

    @patch("src.algo_detective.label_sync.record_scraped_post")
    @patch("src.algo_detective.label_sync.compute_and_store_for_date")
    @patch("src.algo_detective.label_sync.get_computed_prime_pairs")
    @patch("src.algo_detective.label_sync.fetch_recap_trades")
    @patch("src.algo_detective.label_sync.fetch_post_index")
    @patch("src.algo_detective.label_sync.get_scraped_slugs")
    def test_does_not_checkpoint_slug_when_parsing_raises(
        self,
        mock_known,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
    ):
        mock_known.return_value = set()
        mock_index.return_value = ["results_boring_puts_2026_07_13"]
        mock_trades.side_effect = RuntimeError("mLabs changed their HTML structure")
        mock_pairs.return_value = set()

        count = sync_new_labels()

        mock_record.assert_not_called()
        assert count == 0
