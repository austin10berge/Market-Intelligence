"""Tests for compute_and_store_for_date in src/algo_detective/build.py —
the shared feature-computation helper extracted from run_build()'s
per-date loop, reused by control_sync.py and label_sync.py (Tasks 4-5).
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.build import compute_and_store_for_date


def _make_ohlcv(periods: int = 220) -> pd.DataFrame:
    closes = [100.0 + i * 0.1 for i in range(periods)]
    dates = pd.date_range(end="2026-02-02", periods=periods, freq="B")
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
def _patched_dependencies():
    ohlcv = _make_ohlcv()
    with (
        patch("src.algo_detective.build.get_fundamentals_for_tickers") as mock_fund,
        patch("src.algo_detective.build.load_ohlcv_batch_for_date") as mock_ohlcv,
        patch("src.algo_detective.build.compute_macro_for_date") as mock_macro,
        patch("src.algo_detective.build.upsert_macro_row"),
        patch("src.algo_detective.build.upsert_feature_rows_bulk") as mock_upsert,
    ):
        mock_fund.return_value = [
            {"symbol": "AEO", "sector": "Consumer Cyclical", "market_cap_b": 3.0},
            {"symbol": "SPY", "sector": None, "market_cap_b": 400.0},
        ]
        mock_ohlcv.return_value = {"AEO": ohlcv.copy(), "SPY": ohlcv.copy()}
        mock_macro.return_value = None
        mock_upsert.side_effect = lambda rows: len(rows)
        yield


class TestComputeAndStoreForDate:
    def test_computes_and_upserts_rows_for_each_ticker(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("AEO", 1), ("SPY", 0)],
            computed_pairs=set(),
        )
        assert {r["ticker"] for r in rows} == {"AEO", "SPY"}
        assert next(r for r in rows if r["ticker"] == "AEO")["is_prime"] == 1
        assert next(r for r in rows if r["ticker"] == "SPY")["is_prime"] == 0

    def test_skips_pairs_already_in_computed_pairs(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("AEO", 1), ("SPY", 0)],
            computed_pairs={("2026-02-02", "SPY")},
        )
        assert {r["ticker"] for r in rows} == {"AEO"}

    def test_skips_ticker_with_no_ohlcv_in_batch_and_no_fallback(self, _patched_dependencies):
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("UNKNOWN", 1)],
            computed_pairs=set(),
        )
        assert rows == []

    def test_uses_fallback_when_ticker_missing_from_batch(self, _patched_dependencies):
        fallback_df = _make_ohlcv()
        rows = compute_and_store_for_date(
            "2026-02-02",
            [("SMALLCAP", 1)],
            computed_pairs=set(),
            ohlcv_fallback_fn=lambda ticker: fallback_df.copy(),
        )
        assert {r["ticker"] for r in rows} == {"SMALLCAP"}

    def test_returns_empty_list_when_nothing_to_compute(self, _patched_dependencies):
        rows = compute_and_store_for_date("2026-02-02", [], computed_pairs=set())
        assert rows == []
