"""Tests for the algo_detective steps (5, 6, 7) wired into src/main.py's
nightly pipeline: Step 5's ensure_tables() fix, and the new Step 6 (mLabs
label sync) / Step 7 (control universe sync), in that order, both
non-fatal on failure.
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_step5_calls_ensure_tables_before_get_all_features():
    call_order = []
    with (
        patch(
            "src.algo_detective.store.ensure_tables",
            side_effect=lambda: call_order.append("ensure_tables"),
        ) as mock_ensure,
        patch(
            "src.algo_detective.store.get_all_features",
            side_effect=lambda: call_order.append("get_all_features") or [],
        ) as mock_get,
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        mock_ensure.assert_called_once()
        mock_get.assert_called_once()
        assert call_order == ["ensure_tables", "get_all_features"]


@pytest.mark.asyncio
async def test_step6_runs_before_step7():
    call_order = []
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch("src.algo_detective.store.get_all_features", return_value=[]),
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch(
            "src.algo_detective.label_sync.sync_new_labels",
            side_effect=lambda: call_order.append("label_sync") or 0,
        ),
        patch(
            "src.algo_detective.control_sync.sync_control_universe",
            side_effect=lambda d: call_order.append("control_sync") or 0,
        ),
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        assert call_order == ["label_sync", "control_sync"]


@pytest.mark.asyncio
async def test_step6_failure_does_not_block_step7():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch("src.algo_detective.store.get_all_features", return_value=[]),
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch("src.algo_detective.label_sync.sync_new_labels", side_effect=RuntimeError("boom")),
        patch(
            "src.algo_detective.control_sync.sync_control_universe", return_value=0
        ) as mock_control,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_control.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_tables_failure_does_not_block_label_and_control_sync():
    with (
        patch("src.algo_detective.store.ensure_tables", side_effect=RuntimeError("boom")),
        patch("src.algo_detective.store.get_all_features", return_value=[]),
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0) as mock_label,
        patch(
            "src.algo_detective.control_sync.sync_control_universe", return_value=0
        ) as mock_control,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_label.assert_called_once()
        mock_control.assert_called_once()


@pytest.mark.asyncio
async def test_step8_runs_after_step5_with_narrow_universe_tickers():
    call_order = []
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[
                {"ticker": "HOOD", "is_prime": 1},
                {"ticker": "AAPL", "is_prime": 0},
            ],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch(
            "src.algo_detective.options_chain.fetch_snapshot_pcr",
            side_effect=lambda tickers, d: call_order.append(("options_snapshot", tickers)) or 0,
        ),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot",
            side_effect=lambda tickers, d: call_order.append(("delta_snapshot", tickers)) or 0,
        ),
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())

        assert [name for name, _ in call_order] == ["options_snapshot", "delta_snapshot"]
        assert call_order[0][1] == ["HOOD"]
        assert call_order[1][1] == ["HOOD"]


@pytest.mark.asyncio
async def test_step8_failure_is_non_fatal():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[{"ticker": "HOOD", "is_prime": 1}],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr", return_value=0),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot",
            side_effect=RuntimeError("boom"),
        ) as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_delta.assert_called_once()


@pytest.mark.asyncio
async def test_step5_failure_does_not_block_step8():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch(
            "src.algo_detective.store.get_all_features",
            return_value=[{"ticker": "HOOD", "is_prime": 1}],
        ),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0),
        patch("src.algo_detective.control_sync.sync_control_universe", return_value=0),
        patch(
            "src.algo_detective.options_chain.fetch_snapshot_pcr",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "src.algo_detective.schwab_options.fetch_delta_snapshot", return_value=0
        ) as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_delta.assert_called_once()


@pytest.mark.asyncio
async def test_narrow_universe_lookup_failure_does_not_block_label_and_control_sync():
    with (
        patch("src.algo_detective.store.ensure_tables"),
        patch("src.algo_detective.store.get_all_features", side_effect=RuntimeError("boom")),
        patch("src.algo_detective.label_sync.sync_new_labels", return_value=0) as mock_label,
        patch(
            "src.algo_detective.control_sync.sync_control_universe", return_value=0
        ) as mock_control,
        patch("src.algo_detective.options_chain.fetch_snapshot_pcr") as mock_snapshot,
        patch("src.algo_detective.schwab_options.fetch_delta_snapshot") as mock_delta,
    ):
        from src.main import _run_algo_detective_steps

        await _run_algo_detective_steps(date.today())  # must not raise

        mock_label.assert_called_once()
        mock_control.assert_called_once()
        mock_snapshot.assert_not_called()  # narrow_universe stayed [] after the failure
        mock_delta.assert_not_called()
