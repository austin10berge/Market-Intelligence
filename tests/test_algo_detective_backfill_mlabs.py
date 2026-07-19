"""Tests for the two-phase mLabs backfill CLI in
src/algo_detective/backfill_mlabs.py. Phase 1 scrapes every historical
results_boring_puts_* post (idempotent — safe to re-run) into is_prime=1
rows; Phase 2 runs a historical control-universe sync for every distinct
date Phase 1 touched.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

from unittest.mock import patch

from src.algo_detective.backfill_mlabs import run_backfill


class TestRunBackfill:
    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_prime_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_phase1_processes_every_slug_regardless_of_checkpoint(
        self,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
        mock_control,
    ):
        mock_index.return_value = [
            "results_boring_puts_2025_09_01",
            "results_boring_puts_2026_01_05",
        ]
        mock_trades.side_effect = [
            [],  # PDF-era post
            [{"ticker": "NVO", "open_date": "2026-01-05"}],
        ]
        mock_pairs.return_value = set()
        mock_compute.return_value = [{"ticker": "NVO", "date": "2026-01-05", "is_prime": 1}]
        mock_control.return_value = 40

        result = run_backfill()

        assert mock_trades.call_count == 2
        assert mock_record.call_count == 2
        assert result["prime_rows_written"] == 1

    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_prime_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_phase2_runs_control_sync_only_for_dates_phase1_touched(
        self,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
        mock_control,
    ):
        mock_index.return_value = ["results_boring_puts_2026_01_05"]
        mock_trades.return_value = [
            {"ticker": "NVO", "open_date": "2026-01-05"},
            {"ticker": "AAPL", "open_date": "2026-01-07"},
        ]
        mock_pairs.return_value = set()
        mock_compute.side_effect = lambda date, flags, pairs, ohlcv_fallback_fn=None: [
            {"ticker": t, "date": date, "is_prime": f} for t, f in flags
        ]
        mock_control.return_value = 0

        run_backfill()

        control_dates = {c.args[0] for c in mock_control.call_args_list}
        assert control_dates == {"2026-01-05", "2026-01-07"}

    @patch("src.algo_detective.backfill_mlabs.sync_control_universe")
    @patch("src.algo_detective.backfill_mlabs.record_scraped_post")
    @patch("src.algo_detective.backfill_mlabs.compute_and_store_for_date")
    @patch("src.algo_detective.backfill_mlabs.get_computed_prime_pairs")
    @patch("src.algo_detective.backfill_mlabs.fetch_recap_trades")
    @patch("src.algo_detective.backfill_mlabs.fetch_post_index")
    def test_returns_summary_dict(
        self,
        mock_index,
        mock_trades,
        mock_pairs,
        mock_compute,
        mock_record,
        mock_control,
    ):
        mock_index.return_value = []
        mock_control.return_value = 0

        result = run_backfill()

        assert set(result) == {"prime_rows_written", "dates_backfilled", "control_rows_written"}
