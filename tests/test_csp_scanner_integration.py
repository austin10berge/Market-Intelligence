"""Integration tests: CSP scanner reads from local SQLite store instead of yfinance.

Tests verify:
  1. apply_fundamental_filter reads from the local store (not yfinance) when ≥50 rows
     are present, and yfinance is never called in that path.
  2. run_csp_scan returns data_source="local_store" when ≥50 fundamentals are seeded.
  3. run_csp_scan output has the expected shape: candidates, filter_summary, warnings keys.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# ── Shared temp DB ─────────────────────────────────────────────────────────────
# Create once for the module; patched via autouse fixture below.
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    """Redirect all store operations to the temp DB for this module."""
    with patch("src.market_data.store.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup_db():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


# ── Imports (after conftest stubs pandas_ta) ──────────────────────────────────
from src.market_data.store import (
    ensure_tables,
    bulk_upsert_fundamentals,
    bulk_upsert_ohlcv,
)
from src.screener.csp_scanner import (
    apply_fundamental_filter,
    run_csp_scan,
    ScannerParams,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

# Tickers that pass all default filters:
#   market_cap_b > 10.0 (default min)
#   0 < price < 150.0  (default max)
#   0.8 <= beta <= 2.4 (default range)
#   iv_pct set high so vol filter passes
_PASSING_VALUES = dict(market_cap_b=50.0, price=100.0, beta=1.2, iv_pct=35.0)

# Generate 60 test ticker symbols so len(store_lookup) >= 50
_TEST_TICKERS = [f"T{i:03d}" for i in range(60)]


def _seed_fundamentals() -> None:
    """Insert 60 fundamentals rows that pass all default filter thresholds."""
    ensure_tables()
    rows = [
        {"symbol": sym, **_PASSING_VALUES}
        for sym in _TEST_TICKERS
    ]
    bulk_upsert_fundamentals(rows)


def _make_ohlcv_df(n: int = 30) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with a tz-aware DatetimeIndex."""
    dates = pd.bdate_range(start="2024-01-02", periods=n, tz="America/New_York")
    price = 100.0
    return pd.DataFrame(
        {
            "Open":   [price] * n,
            "High":   [price * 1.01] * n,
            "Low":    [price * 0.99] * n,
            "Close":  [price] * n,
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


def _seed_ohlcv() -> None:
    """Insert OHLCV data for each test ticker."""
    df = _make_ohlcv_df()
    for sym in _TEST_TICKERS:
        bulk_upsert_ohlcv(sym, df)


# ── Test: apply_fundamental_filter reads from store, not yfinance ─────────────

class TestApplyFundamentalFilterUsesStore:
    """apply_fundamental_filter must use the local store when ≥50 rows exist."""

    def setup_method(self):
        _seed_fundamentals()

    def test_yfinance_not_called_when_store_populated(self):
        """yf.Ticker must never be instantiated when the store has ≥50 rows."""
        params = ScannerParams()
        tickers_to_scan = _TEST_TICKERS[:10]  # a subset

        with patch("src.screener.csp_scanner.yf") as mock_yf:
            passing, rows = apply_fundamental_filter(tickers_to_scan, params)

        # yf.Ticker should never have been called
        mock_yf.Ticker.assert_not_called()

    def test_results_come_from_seeded_data(self):
        """Results should match what we seeded — all 10 tickers pass, yfinance never called."""
        params = ScannerParams()
        tickers_to_scan = _TEST_TICKERS[:10]

        with patch("src.screener.csp_scanner.yf") as mock_yf:
            passing, rows = apply_fundamental_filter(tickers_to_scan, params)

        # Store path taken — yfinance never touched
        mock_yf.Ticker.assert_not_called()

        # All seeded tickers should pass (values chosen to clear every threshold)
        assert set(passing) == set(tickers_to_scan)
        assert len(rows) == 10

        # Spot-check one row's fields match the seeded values
        row_map = {r["symbol"]: r for r in rows}
        first = row_map[tickers_to_scan[0]]
        assert first["market_cap_b"] == pytest.approx(_PASSING_VALUES["market_cap_b"])
        assert first["price"] == pytest.approx(_PASSING_VALUES["price"])
        assert first["beta"] == pytest.approx(_PASSING_VALUES["beta"])
        assert first["iv"] == pytest.approx(_PASSING_VALUES["iv_pct"])

    def test_ticker_not_in_store_is_skipped(self):
        """A ticker absent from the store is simply skipped (no fallback to yfinance)."""
        params = ScannerParams()
        tickers_to_scan = _TEST_TICKERS[:5] + ["UNKNOWN_XYZ"]

        with patch("src.screener.csp_scanner.yf") as mock_yf:
            passing, rows = apply_fundamental_filter(tickers_to_scan, params)

        mock_yf.Ticker.assert_not_called()
        assert "UNKNOWN_XYZ" not in passing
        # The 5 known tickers should still pass
        assert len(passing) == 5

    def test_tickers_failing_filters_excluded(self):
        """Tickers whose stored values fail a filter threshold should be excluded."""
        ensure_tables()
        # Seed a ticker that fails market_cap (too low) and one that fails price (too high)
        bulk_upsert_fundamentals([
            {"symbol": "LOWCAP", "market_cap_b": 1.0, "price": 100.0, "beta": 1.2, "iv_pct": 35.0},
            {"symbol": "HIPRCE", "market_cap_b": 50.0, "price": 200.0, "beta": 1.2, "iv_pct": 35.0},
            {"symbol": "BADBETA", "market_cap_b": 50.0, "price": 100.0, "beta": 0.1, "iv_pct": 35.0},
        ])

        params = ScannerParams()
        passing, _ = apply_fundamental_filter(
            ["LOWCAP", "HIPRCE", "BADBETA"], params
        )

        assert "LOWCAP" not in passing
        assert "HIPRCE" not in passing
        assert "BADBETA" not in passing


# ── Test: run_csp_scan returns data_source="local_store" ─────────────────────

class TestRunCspScanDataSource:
    """run_csp_scan reports data_source='local_store' when store has ≥50 fundamentals."""

    def setup_method(self):
        _seed_fundamentals()
        _seed_ohlcv()

    def test_data_source_is_local_store(self):
        """run_csp_scan must report local_store when ≥50 fundamentals are seeded."""
        params = ScannerParams()

        with (
            patch("src.screener.csp_scanner.fetch_sp500_tickers", return_value=_TEST_TICKERS[:30]),
            patch("src.screener.csp_scanner.fetch_nasdaq100_tickers", return_value=_TEST_TICKERS[30:60]),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert result["data_source"] == "local_store"

    def test_output_has_expected_shape(self):
        """Output must contain candidates, filter_summary, warnings, and data_source keys."""
        params = ScannerParams()

        with (
            patch("src.screener.csp_scanner.fetch_sp500_tickers", return_value=_TEST_TICKERS[:30]),
            patch("src.screener.csp_scanner.fetch_nasdaq100_tickers", return_value=_TEST_TICKERS[30:60]),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert "candidates" in result
        assert "filter_summary" in result
        assert "warnings" in result
        assert result["data_source"] == "local_store"

    def test_filter_summary_has_expected_keys(self):
        """filter_summary must expose the stage-by-stage counts."""
        params = ScannerParams()

        with (
            patch("src.screener.csp_scanner.fetch_sp500_tickers", return_value=_TEST_TICKERS[:30]),
            patch("src.screener.csp_scanner.fetch_nasdaq100_tickers", return_value=_TEST_TICKERS[30:60]),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert result["data_source"] == "local_store"
        summary = result["filter_summary"]
        expected_keys = {
            "sp500_count", "nasdaq100_count", "combined_unique",
            "fundamental_passed", "vol_passed", "technical_passed",
            "options_screener_returned",
        }
        assert expected_keys.issubset(summary.keys())

    def test_no_http_requests_during_scan(self):
        """Neither yfinance nor the index fetchers should make real HTTP calls."""
        params = ScannerParams()

        with (
            patch("src.screener.csp_scanner.fetch_sp500_tickers", return_value=_TEST_TICKERS[:30]),
            patch("src.screener.csp_scanner.fetch_nasdaq100_tickers", return_value=_TEST_TICKERS[30:60]),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
            patch("src.screener.csp_scanner.yf") as mock_yf,
        ):
            result = run_csp_scan(params)

        # The fundamental filter should have used the store; yf never called
        mock_yf.Ticker.assert_not_called()
