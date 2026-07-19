"""Tests for compute_and_store_for_date in src/algo_detective/build.py —
the shared feature-computation helper extracted from run_build()'s
per-date loop, reused by control_sync.py and label_sync.py (Tasks 4-5).
See docs/superpowers/specs/2026-07-19-algo-detective-automated-pipeline-design.md.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import patch

import pandas as pd
import pytest

from src.algo_detective.build import compute_and_store_for_date
from src.algo_detective.store import ensure_tables, get_computed_pairs, get_computed_prime_pairs


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


class TestPrimePromotionRegression:
    """Real-SQLite regression test for the final-review CRITICAL finding:
    a control (is_prime=0) row already stored for a (date, ticker) pair
    must never permanently block a later attempt to promote that same
    pair to is_prime=1 (this is the normal case once the weekly mLabs
    recap post for an already-tracked control ticker is scraped).

    Uses a real temp SQLite DB (not mocked upserts) so the interaction
    between compute_and_store_for_date's computed_pairs pre-filter and
    upsert_feature_rows_bulk's ON CONFLICT upsert is actually exercised,
    per the final review's explicit recommendation.
    """

    @pytest.fixture
    def _real_db(self, tmp_path):
        db_path = tmp_path / "prime_promotion_test.db"
        with patch("src.algo_detective.store.settings") as mock_settings:
            mock_settings.db_path = str(db_path)
            ensure_tables()
            yield db_path

    @pytest.fixture
    def _patched_feature_inputs(self):
        ohlcv = _make_ohlcv()
        with (
            patch("src.algo_detective.build.get_fundamentals_for_tickers") as mock_fund,
            patch("src.algo_detective.build.load_ohlcv_batch_for_date") as mock_ohlcv,
            patch("src.algo_detective.build.compute_macro_for_date") as mock_macro,
            patch("src.algo_detective.build.upsert_macro_row"),
        ):
            mock_fund.return_value = [
                {"symbol": "AEO", "sector": "Consumer Cyclical", "market_cap_b": 3.0},
            ]
            mock_ohlcv.return_value = {"AEO": ohlcv.copy()}
            mock_macro.return_value = None
            yield

    def _is_prime_in_db(self, db_path, date, ticker) -> int:
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT is_prime FROM detective_features WHERE date = ? AND ticker = ?",
                (date, ticker),
            ).fetchone()
            return row[0]
        finally:
            conn.close()

    def test_prime_only_pairs_allow_promoting_an_existing_control_row(
        self, _real_db, _patched_feature_inputs
    ):
        date = "2026-02-02"

        # Step 1 (control_sync's nightly run): AEO gets written as
        # is_prime=0 because it's just a normal member of the tracked
        # universe on `date`.
        control_rows = compute_and_store_for_date(date, [("AEO", 0)], computed_pairs=set())
        assert control_rows[0]["is_prime"] == 0
        assert self._is_prime_in_db(_real_db, date, "AEO") == 0

        # Step 2 (days/weeks later): the mLabs recap post covering `date`
        # is scraped and label_sync tries to promote AEO to is_prime=1.
        # Using get_computed_prime_pairs() (the fix) means the existing
        # control row does NOT block the attempt.
        prime_pairs = get_computed_prime_pairs()
        assert (date, "AEO") not in prime_pairs

        promoted_rows = compute_and_store_for_date(date, [("AEO", 1)], computed_pairs=prime_pairs)
        assert promoted_rows[0]["is_prime"] == 1
        assert self._is_prime_in_db(_real_db, date, "AEO") == 1

    def test_blanket_computed_pairs_incorrectly_blocks_promotion(
        self, _real_db, _patched_feature_inputs
    ):
        """Documents the bug being fixed: passing the OLD blanket
        get_computed_pairs() set (instead of the prime-only set) causes
        the promotion attempt to be silently skipped, leaving the row
        stuck at is_prime=0 forever."""
        date = "2026-02-02"
        compute_and_store_for_date(date, [("AEO", 0)], computed_pairs=set())
        assert self._is_prime_in_db(_real_db, date, "AEO") == 0

        blanket_pairs = get_computed_pairs()
        assert (date, "AEO") in blanket_pairs  # pre-existing control row is in here

        buggy_rows = compute_and_store_for_date(date, [("AEO", 1)], computed_pairs=blanket_pairs)
        assert buggy_rows == []  # promotion attempt silently skipped
        assert self._is_prime_in_db(_real_db, date, "AEO") == 0  # still stuck at control
