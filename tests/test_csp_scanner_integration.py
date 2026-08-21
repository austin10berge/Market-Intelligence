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
    """Redirect all store operations to the temp DB for this module.

    Also isolates src.db.get_watchlist(): it reads from settings.db_path via a
    separate settings reference (src.db.settings, not src.market_data.store.settings)
    and falls back to a hardcoded default watchlist of 21 real tickers (AAPL, MSFT,
    ...) when no 'watchlist' row exists in app_config -- which is always the case
    for the temp DB used here. Left unpatched, those real tickers leak into the scan
    universe via run_csp_scan's watchlist-merge step and are looked up individually
    against the (empty, for those tickers) local store, triggering the very
    per-ticker yfinance fallback these tests assert never happens.
    """
    with (
        patch("src.market_data.store.settings") as mock_settings,
        patch("src.db.settings") as mock_db_settings,
        patch("src.screener.csp_scanner.get_watchlist", return_value=[]),
    ):
        mock_settings.db_path = _tmp_db_path
        mock_db_settings.db_path = _tmp_db_path
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
    _fundamental_filter_from_store,
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
        """A ticker absent from the store falls back to a live per-ticker yfinance
        lookup (deliberate, see commit 1d7a68a "fix: fall back to yfinance for
        tickers missing from fundamentals store") rather than being silently
        dropped. With yf mocked, the mocked `.info` doesn't look like a real
        EQUITY quote, so the missing ticker still fails the fundamental filter
        and does not appear in `passing` -- but yf.Ticker *is* called for it.
        """
        params = ScannerParams()
        tickers_to_scan = _TEST_TICKERS[:5] + ["UNKNOWN_XYZ"]

        with patch("src.screener.csp_scanner.yf") as mock_yf:
            passing, rows = apply_fundamental_filter(tickers_to_scan, params)

        mock_yf.Ticker.assert_called_once_with("UNKNOWN_XYZ")
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

    def test_watchlist_only_skips_universe_and_fundamentals_gates(self):
        """With watchlist_only=True, none of the universe (cap/price/beta) or
        fundamentals (FCF/D-E/etc.) gates should exclude the user's own curated
        watchlist tickers -- a $600 ETF, a beta-0.1 utility, or a negative-FCF
        growth stock the user chose deliberately should still reach the
        vol/technical gates rather than being screened out here.
        """
        ensure_tables()
        bulk_upsert_fundamentals([
            {"symbol": "LOWCAP", "market_cap_b": 1.0, "price": 100.0, "beta": 1.2, "iv_pct": 35.0},
            {"symbol": "HIPRCE", "market_cap_b": 50.0, "price": 200.0, "beta": 1.2, "iv_pct": 35.0},
            {"symbol": "BADBETA", "market_cap_b": 50.0, "price": 100.0, "beta": 0.1, "iv_pct": 35.0},
            {"symbol": "NEGFCF", "market_cap_b": 50.0, "price": 100.0, "beta": 1.2,
             "iv_pct": 35.0, "fcf": -5.0},
        ])

        params = ScannerParams(watchlist_only=True)
        passing, _ = apply_fundamental_filter(
            ["LOWCAP", "HIPRCE", "BADBETA", "NEGFCF"], params
        )

        assert set(passing) == {"LOWCAP", "HIPRCE", "BADBETA", "NEGFCF"}

    def test_value_screen_preset_gates_end_to_end(self):
        """End-to-end: seed the real store with the Reddit Value Screen preset's
        fields and confirm apply_fundamental_filter (the real store-reading code
        path, not the isolated gate-logic unit tests in test_fundamental_filter.py)
        applies all 4 preset gates correctly through a full store round-trip.
        """
        ensure_tables()
        bulk_upsert_fundamentals([
            {
                "symbol": "PASSVAL",
                **_PASSING_VALUES,
                "revenue_growth": 0.15,
                "peg_ratio": 1.2,
                "gross_margin": 0.50,
                "interest_coverage": 6.0,
            },
            {
                "symbol": "FAILVAL",
                **_PASSING_VALUES,
                "revenue_growth": 0.15,
                "peg_ratio": 1.2,
                "gross_margin": 0.25,  # fails min_gross_margin=0.40
                "interest_coverage": 6.0,
            },
        ])

        params = ScannerParams(
            min_interest_coverage=4.0,
            min_gross_margin=0.40,
            min_revenue_growth=0.10,
            max_peg_ratio=1.5,
        )
        passing, _ = apply_fundamental_filter(["PASSVAL", "FAILVAL"], params)

        assert "PASSVAL" in passing
        assert "FAILVAL" not in passing


# ── Test: run_csp_scan returns data_source="local_store" ─────────────────────

class TestRunCspScanDataSource:
    """run_csp_scan reports data_source='local_store' when store has ≥50 fundamentals."""

    def setup_method(self):
        _seed_fundamentals()
        _seed_ohlcv()

    def test_data_source_is_local_store(self):
        """run_csp_scan must report local_store when ≥50 fundamentals are seeded."""
        params = ScannerParams(min_adx=0.0, max_adx=100.0)

        with (
            patch("src.screener.csp_scanner.fetch_universe", return_value=_TEST_TICKERS),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert result["data_source"] == "local_store"

    def test_output_has_expected_shape(self):
        """Output must contain candidates, filter_summary, warnings, and data_source keys."""
        params = ScannerParams(min_adx=0.0, max_adx=100.0)

        with (
            patch("src.screener.csp_scanner.fetch_universe", return_value=_TEST_TICKERS),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert "candidates" in result
        assert "filter_summary" in result
        assert "warnings" in result
        assert result["data_source"] == "local_store"

    def test_filter_summary_has_expected_keys(self):
        """filter_summary must expose the stage-by-stage counts."""
        params = ScannerParams(min_adx=0.0, max_adx=100.0)

        with (
            patch("src.screener.csp_scanner.fetch_universe", return_value=_TEST_TICKERS),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        assert result["data_source"] == "local_store"
        summary = result["filter_summary"]
        expected_keys = {
            "combined_unique",
            "fundamental_passed", "vol_passed", "technical_passed",
            "options_screener_returned",
        }
        assert expected_keys.issubset(summary.keys())

    def test_no_http_requests_during_scan(self):
        """Neither yfinance nor the index fetchers should make real HTTP calls."""
        params = ScannerParams(min_adx=0.0, max_adx=100.0)

        with (
            patch("src.screener.csp_scanner.fetch_universe", return_value=_TEST_TICKERS),
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
            patch("src.screener.csp_scanner.yf") as mock_yf,
        ):
            result = run_csp_scan(params)

        # The fundamental filter should have used the store; yf never called
        mock_yf.Ticker.assert_not_called()


# ── Test: fetch_nasdaq_large_cap_tickers ─────────────────────────────────────

MOCK_NASDAQ_SCREENER_RESPONSE = {
    "data": {
        "table": {
            "rows": [
                {"symbol": "AAPL", "marketCap": "2,700,000,000,000"},
                {"symbol": "MSFT", "marketCap": "3,000,000,000,000"},
                {"symbol": "SMLC", "marketCap": "500,000,000"},     # < $2B — filtered out
                {"symbol": "SMLL", "marketCap": "1,999,999,999"},   # < $2B — filtered out
                {"symbol": "MIDC", "marketCap": "2,000,000,001"},   # just over $2B — kept
            ]
        }
    }
}


def test_fetch_nasdaq_large_cap_tickers_filters_by_market_cap():
    mock_resp = MagicMock()
    mock_resp.json.return_value = MOCK_NASDAQ_SCREENER_RESPONSE
    mock_resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=mock_resp):
        from src.screener.csp_scanner import fetch_nasdaq_large_cap_tickers
        tickers = fetch_nasdaq_large_cap_tickers()
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "MIDC" in tickers
    assert "SMLC" not in tickers
    assert "SMLL" not in tickers
    assert tickers == sorted(tickers)  # must be sorted


def test_fetch_nasdaq_large_cap_tickers_returns_empty_on_api_failure():
    with patch("requests.get", side_effect=Exception("connection error")):
        from src.screener.csp_scanner import fetch_nasdaq_large_cap_tickers
        result = fetch_nasdaq_large_cap_tickers()
    assert result == []


# ── Test: restrict_to_watchlist_universe parameter ───────────────────────────────

def test_restrict_to_watchlist_universe_filters_correctly():
    """When restrict_to_watchlist_universe=True, only sp500/nasdaq100 rows pass."""
    store_lookup = {
        "AAPL": {"symbol": "AAPL", "market_cap_b": 100.0, "price": 150.0, "beta": 1.0,
                 "iv_pct": 30.0, "fcf": 10.0, "forward_pe": 20.0, "universes": "nasdaq100,sp500"},
        "AMZN": {"symbol": "AMZN", "market_cap_b": 50.0, "price": 120.0, "beta": 1.1,
                 "iv_pct": 35.0, "fcf": 8.0, "forward_pe": 25.0, "universes": "nasdaq_large"},
        "NEWC": {"symbol": "NEWC", "market_cap_b": 5.0, "price": 40.0, "beta": 1.2,
                 "iv_pct": 40.0, "fcf": 1.0, "forward_pe": 15.0, "universes": "nasdaq_large"},
    }
    params_restricted = ScannerParams(
        min_market_cap_b=1.0, max_price=500.0, min_beta=0.5, max_beta=3.0,
        min_fcf_b=None, max_debt_to_equity=None, min_revenue_growth=None,
        restrict_to_watchlist_universe=True,
    )
    tickers, rows = _fundamental_filter_from_store(
        list(store_lookup.keys()), params_restricted, store_lookup
    )
    assert "AAPL" in tickers
    assert "AMZN" not in tickers
    assert "NEWC" not in tickers


def test_restrict_to_watchlist_universe_false_passes_all():
    """When restrict_to_watchlist_universe=False (default), all passing tickers pass."""
    store_lookup = {
        "AAPL": {"symbol": "AAPL", "market_cap_b": 100.0, "price": 150.0, "beta": 1.0,
                 "iv_pct": 30.0, "fcf": 10.0, "forward_pe": 20.0, "universes": "nasdaq100,sp500"},
        "AMZN": {"symbol": "AMZN", "market_cap_b": 50.0, "price": 120.0, "beta": 1.1,
                 "iv_pct": 35.0, "fcf": 8.0, "forward_pe": 25.0, "universes": "nasdaq_large"},
    }
    params_open = ScannerParams(
        min_market_cap_b=1.0, max_price=500.0, min_beta=0.5, max_beta=3.0,
        min_fcf_b=None, max_debt_to_equity=None, min_revenue_growth=None,
        restrict_to_watchlist_universe=False,
    )
    tickers, rows = _fundamental_filter_from_store(
        list(store_lookup.keys()), params_open, store_lookup
    )
    assert "AAPL" in tickers
    assert "AMZN" in tickers


def test_scanner_params_cache_key_differs_by_universe_flag():
    p1 = ScannerParams(restrict_to_watchlist_universe=False)
    p2 = ScannerParams(restrict_to_watchlist_universe=True)
    assert p1.cache_key_suffix() != p2.cache_key_suffix()


# ── Test: watchlist_only parameter ────────────────────────────────────────────

class TestWatchlistOnlyParam:
    """run_csp_scan(watchlist_only=True) must scan only the user's CSP watchlist,
    never the broad S&P 500 / NASDAQ 100 / large-cap universe.
    """

    def setup_method(self):
        _seed_fundamentals()
        _seed_ohlcv()

    def test_watchlist_only_bypasses_fetch_universe(self):
        """fetch_universe() must never be called when watchlist_only=True."""
        params = ScannerParams(min_adx=0.0, max_adx=100.0, watchlist_only=True)
        watchlist_subset = _TEST_TICKERS[:5]

        with (
            patch("src.screener.csp_scanner.get_watchlist", return_value=watchlist_subset),
            patch("src.screener.csp_scanner.fetch_universe") as mock_fetch_universe,
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        mock_fetch_universe.assert_not_called()
        assert result["filter_summary"]["combined_unique"] == 5

    def test_watchlist_only_empty_watchlist_returns_warning(self):
        """An empty watchlist with watchlist_only=True returns zero candidates
        and a warning, instead of silently falling back to the broad universe.
        """
        params = ScannerParams(min_adx=0.0, max_adx=100.0, watchlist_only=True)

        with (
            patch("src.screener.csp_scanner.get_watchlist", return_value=[]),
            patch("src.screener.csp_scanner.fetch_universe") as mock_fetch_universe,
        ):
            result = run_csp_scan(params)

        mock_fetch_universe.assert_not_called()
        assert result["candidates"] == []
        assert any("watchlist" in w.lower() for w in result["warnings"])

    def test_watchlist_only_false_uses_broad_universe(self):
        """Default behavior (watchlist_only=False) is unaffected — fetch_universe
        is still called and its result still drives the scan universe.
        """
        params = ScannerParams(min_adx=0.0, max_adx=100.0, watchlist_only=False)

        with (
            patch("src.screener.csp_scanner.get_watchlist", return_value=[]),
            patch(
                "src.screener.csp_scanner.fetch_universe", return_value=_TEST_TICKERS
            ) as mock_fetch_universe,
            patch("src.screener.csp_scanner.screen_csp_candidates", return_value=[]),
        ):
            result = run_csp_scan(params)

        mock_fetch_universe.assert_called_once()
        assert result["filter_summary"]["combined_unique"] == len(_TEST_TICKERS)

    def test_scanner_params_cache_key_differs_by_watchlist_only_flag(self):
        p1 = ScannerParams(watchlist_only=False)
        p2 = ScannerParams(watchlist_only=True)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()

    def test_from_query_threads_watchlist_only(self):
        assert ScannerParams.from_query(watchlist_only=True).watchlist_only is True
        assert ScannerParams.from_query().watchlist_only is False
