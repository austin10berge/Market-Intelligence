"""Unit tests for src.market_data.refresh — daily OHLCV + fundamentals refresh job.

All yfinance network calls are mocked.  The SQLite store is redirected to a
temp file using the same pattern as test_market_data_store.py.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ── Temp DB setup (must happen before importing the module under test) ─────────

_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    """Redirect all store DB operations to a temp file."""
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


# ── Import after patching ─────────────────────────────────────────────────────

from src.market_data.refresh import (
    refresh_universe,
    _download_ohlcv_batch,
    _fetch_fundamentals_batch,
)
from src.market_data.store import ensure_tables, get_ohlcv, get_all_fundamentals


# ── Fixtures / helpers ─────────────────────────────────────────────────────────

SMALL_UNIVERSE = ["AAPL", "MSFT", "GOOG"]


def _make_ohlcv_df(n: int = 5, start_date: str = "2024-01-02") -> pd.DataFrame:
    """Return a small OHLCV DataFrame with a DatetimeIndex (tz-naive)."""
    dates = pd.bdate_range(start=start_date, periods=n)
    return pd.DataFrame(
        {
            "Open": [100.0 + i for i in range(n)],
            "High": [101.0 + i for i in range(n)],
            "Low": [99.0 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


def _make_multi_index_df(symbols: list[str], n: int = 5) -> pd.DataFrame:
    """Build a MultiIndex DataFrame mimicking yf.download() output for multiple tickers.

    Column structure: (Price, Ticker) — e.g. ('Close', 'AAPL'), ('Open', 'MSFT').
    """
    dates = pd.bdate_range(start="2024-01-02", periods=n)
    price_cols = ["Open", "High", "Low", "Close", "Volume"]

    arrays = [
        [price for price in price_cols for _ in symbols],
        [sym for _ in price_cols for sym in symbols],
    ]
    multi_cols = pd.MultiIndex.from_arrays(arrays, names=["Price", "Ticker"])

    data = {}
    for price in price_cols:
        for sym in symbols:
            base = 100.0 if price != "Volume" else 1_000_000.0
            data[(price, sym)] = [base + i for i in range(n)]

    return pd.DataFrame(data, index=dates, columns=multi_cols)


def _make_ticker_info(symbol: str) -> dict:
    """Return a minimal yf.Ticker().info dict for a given symbol."""
    return {
        "quoteType": "EQUITY",
        "marketCap": 3_000_000_000_000,  # 3 000 B
        "currentPrice": 190.0,
        "beta": 1.2,
        "impliedVolatility": 0.285,  # 28.5 %
    }


# ── _download_ohlcv_batch ─────────────────────────────────────────────────────

class TestDownloadOhlcvBatch:
    def test_empty_symbols_returns_empty_dict(self):
        result = _download_ohlcv_batch([])
        assert result == {}

    def test_single_ticker_returns_df(self):
        df = _make_ohlcv_df(n=5)
        with patch("src.market_data.refresh.yf.download", return_value=df) as mock_dl:
            result = _download_ohlcv_batch(["AAPL"], period="5d")

        mock_dl.assert_called_once_with(
            tickers=["AAPL"],
            period="5d",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        assert "AAPL" in result
        assert len(result["AAPL"]) == 5

    def test_multiple_tickers_uses_xs_per_ticker(self):
        symbols = ["AAPL", "MSFT"]
        multi_df = _make_multi_index_df(symbols, n=5)
        with patch("src.market_data.refresh.yf.download", return_value=multi_df):
            result = _download_ohlcv_batch(symbols, period="5d")

        assert set(result.keys()) == {"AAPL", "MSFT"}
        for sym in symbols:
            assert not result[sym].empty
            assert len(result[sym]) == 5

    def test_period_passed_to_yf_download(self):
        df = _make_ohlcv_df(n=10)
        with patch("src.market_data.refresh.yf.download", return_value=df) as mock_dl:
            _download_ohlcv_batch(["AAPL"], period="2y")

        _, kwargs = mock_dl.call_args
        assert kwargs["period"] == "2y"

    def test_empty_download_returns_empty_dict(self):
        empty_df = pd.DataFrame()
        with patch("src.market_data.refresh.yf.download", return_value=empty_df):
            result = _download_ohlcv_batch(["AAPL"], period="5d")

        assert result == {}

    def test_yf_download_exception_returns_empty_dict(self):
        with patch("src.market_data.refresh.yf.download", side_effect=RuntimeError("network error")):
            result = _download_ohlcv_batch(["AAPL", "MSFT"], period="5d")

        assert result == {}

    def test_missing_ticker_in_multiindex_is_skipped(self):
        """If one ticker is absent from the MultiIndex, it should be silently skipped."""
        symbols = ["AAPL", "MISSING"]
        # Build a MultiIndex with only AAPL
        multi_df = _make_multi_index_df(["AAPL"], n=3)
        with patch("src.market_data.refresh.yf.download", return_value=multi_df):
            result = _download_ohlcv_batch(symbols, period="5d")

        assert "AAPL" in result
        assert "MISSING" not in result


# ── _fetch_fundamentals_batch ─────────────────────────────────────────────────

class TestFetchFundamentalsBatch:
    def _mock_ticker(self, info: dict) -> MagicMock:
        mock_ticker = MagicMock()
        mock_ticker.info = info
        return mock_ticker

    def test_returns_row_for_each_equity(self):
        infos = {sym: _make_ticker_info(sym) for sym in SMALL_UNIVERSE}

        def _ticker_factory(symbol):
            return self._mock_ticker(infos[symbol])

        with patch("src.market_data.refresh.yf.Ticker", side_effect=_ticker_factory):
            rows = _fetch_fundamentals_batch(SMALL_UNIVERSE)

        assert len(rows) == 3
        symbols_returned = {r["symbol"] for r in rows}
        assert symbols_returned == set(SMALL_UNIVERSE)

    def test_non_equity_is_excluded(self):
        info = {**_make_ticker_info("ETF1"), "quoteType": "ETF"}
        mock_ticker = self._mock_ticker(info)
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["ETF1"])

        assert rows == []

    def test_field_values_are_correctly_transformed(self):
        info = _make_ticker_info("AAPL")
        mock_ticker = self._mock_ticker(info)
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        row = rows[0]
        assert row["symbol"] == "AAPL"
        assert row["market_cap_b"] == pytest.approx(3000.0)
        assert row["price"] == pytest.approx(190.0)
        assert row["beta"] == pytest.approx(1.2)
        assert row["iv_pct"] == pytest.approx(28.5)

    def test_ticker_exception_is_swallowed(self):
        """A failure on one ticker should not prevent others from being processed."""
        def _ticker_factory(symbol):
            if symbol == "BAD":
                raise RuntimeError("API error")
            return self._mock_ticker(_make_ticker_info(symbol))

        with patch("src.market_data.refresh.yf.Ticker", side_effect=_ticker_factory):
            rows = _fetch_fundamentals_batch(["BAD", "AAPL"])

        symbols_returned = {r["symbol"] for r in rows}
        assert "BAD" not in symbols_returned
        assert "AAPL" in symbols_returned

    def test_none_iv_is_stored_as_none(self):
        info = {**_make_ticker_info("AAPL"), "impliedVolatility": None}
        mock_ticker = self._mock_ticker(info)
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["iv_pct"] is None


def _make_income_stmt_df(ebit: float | None, interest_expense: float | None) -> pd.DataFrame:
    """Mimic yf.Ticker().get_income_stmt(freq='yearly') — one column (latest period),
    indexed by line-item name. Omits a row entirely when its value is None, matching
    how yfinance omits line items a company doesn't report (e.g. InterestExpense for
    debt-free companies)."""
    data = {}
    if ebit is not None:
        data["EBIT"] = ebit
    if interest_expense is not None:
        data["InterestExpense"] = interest_expense
    return pd.DataFrame({"2025-12-31": pd.Series(data)})


class TestFetchFundamentalsBatchGrossMarginAndInterestCoverage:
    def _mock_ticker(self, info: dict, income_stmt: pd.DataFrame | None = None) -> MagicMock:
        mock_ticker = MagicMock()
        mock_ticker.info = info
        mock_ticker.get_income_stmt.return_value = (
            income_stmt if income_stmt is not None else pd.DataFrame()
        )
        return mock_ticker

    def test_gross_margin_read_from_info(self):
        info = {**_make_ticker_info("AAPL"), "grossMargins": 0.48653}
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.81e9, 6.80e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["gross_margin"] == pytest.approx(0.48653)

    def test_gross_margin_missing_is_none(self):
        info = _make_ticker_info("AAPL")  # no grossMargins key
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.81e9, 6.80e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["gross_margin"] is None

    def test_interest_coverage_computed_from_ebit_and_interest_expense(self):
        info = _make_ticker_info("T")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(33.811e9, 6.804e9))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["T"])

        assert rows[0]["interest_coverage"] == pytest.approx(4.969, abs=0.01)

    def test_interest_coverage_none_when_interest_expense_missing(self):
        """Debt-free companies (e.g. AAPL) report no InterestExpense line at all."""
        info = _make_ticker_info("AAPL")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(120e9, None))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None

    def test_interest_coverage_none_when_interest_expense_is_zero(self):
        info = _make_ticker_info("AAPL")
        mock_ticker = self._mock_ticker(info, _make_income_stmt_df(120e9, 0.0))
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None

    def test_interest_coverage_none_when_income_stmt_raises(self):
        """A failure fetching the income statement must not drop the whole ticker."""
        mock_ticker = self._mock_ticker(_make_ticker_info("AAPL"))
        mock_ticker.get_income_stmt.side_effect = RuntimeError("network error")
        with patch("src.market_data.refresh.yf.Ticker", return_value=mock_ticker):
            rows = _fetch_fundamentals_batch(["AAPL"])

        assert rows[0]["interest_coverage"] is None
        assert rows[0]["symbol"] == "AAPL"  # rest of the row still populated


# ── refresh_universe — incremental mode ──────────────────────────────────────

class TestRefreshUniverseIncremental:
    """refresh_universe(full=False) should use period='5d'."""

    def test_mode_label_is_incremental(self):
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=5)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        assert result["mode"] == "incremental"

    def test_yf_download_called_with_5d_period(self):
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=5)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df) as mock_dl,
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            refresh_universe(full=False)

        # Every call to yf.download must use period='5d'
        for c in mock_dl.call_args_list:
            assert c.kwargs["period"] == "5d"

    def test_universe_size_returned(self):
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=5)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["MSFT", "GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        # Set union: AAPL, MSFT, GOOG = 3 unique
        assert result["universe_size"] == 3


# ── refresh_universe — full mode ──────────────────────────────────────────────

class TestRefreshUniverseFull:
    """refresh_universe(full=True) should use period='2y'."""

    def test_mode_label_is_full(self):
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=5)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=True)

        assert result["mode"] == "full"

    def test_yf_download_called_with_2y_period(self):
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=5)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df) as mock_dl,
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            refresh_universe(full=True)

        for c in mock_dl.call_args_list:
            assert c.kwargs["period"] == "2y"


# ── refresh_universe — OHLCV upsert path ─────────────────────────────────────

class TestRefreshUniverseOhlcvUpsert:
    """Verify that OHLCV data returned from yf.download ends up in the DB."""

    def test_ohlcv_rows_are_upserted(self):
        ensure_tables()
        multi_df = _make_multi_index_df(["AAPL", "MSFT"], n=3)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        # 2 tickers × 3 rows each = 6 rows total
        assert result["ohlcv_rows_upserted"] == 6

    def test_ohlcv_data_readable_after_refresh(self):
        ensure_tables()
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            # Single-ticker universe → yf.download returns a flat DataFrame (not MultiIndex)
            patch("src.market_data.refresh.yf.download", return_value=_make_ohlcv_df(n=4)),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            refresh_universe(full=False)

        df = get_ohlcv("AAPL", lookback_days=10)
        assert len(df) >= 1  # at least some rows stored


# ── refresh_universe — fundamentals upsert path ───────────────────────────────

class TestRefreshUniverseFundamentalsUpsert:
    """Verify that fundamentals fetched via yf.Ticker().info end up in the DB."""

    def test_fundamentals_are_upserted(self):
        ensure_tables()
        multi_df = _make_multi_index_df(["AAPL", "MSFT"], n=3)

        def _ticker_factory(symbol):
            mock = MagicMock()
            mock.info = _make_ticker_info(symbol)
            return mock

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", side_effect=_ticker_factory),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        assert result["fundamentals_upserted"] == 2

    def test_fundamentals_readable_after_refresh(self):
        ensure_tables()

        def _ticker_factory(symbol):
            mock = MagicMock()
            mock.info = _make_ticker_info(symbol)
            return mock

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            # Single-ticker universe → flat DataFrame path
            patch("src.market_data.refresh.yf.download", return_value=_make_ohlcv_df(n=2)),
            patch("src.market_data.refresh.yf.Ticker", side_effect=_ticker_factory),
            patch("src.market_data.refresh.time.sleep"),
        ):
            refresh_universe(full=False)

        all_fund = get_all_fundamentals()
        symbols = {r["symbol"] for r in all_fund}
        assert "GOOG" in symbols


# ── refresh_universe — empty universe guard ───────────────────────────────────

def test_refresh_universe_stamps_universes_tag(monkeypatch):
    """refresh_universe() should tag each fundamental row with its universe membership."""
    ensure_tables()

    # Stub universe fetchers: AAPL in all three, MSFT in sp500+nasdaq100, AMZN only in nasdaq_large
    monkeypatch.setattr("src.market_data.refresh.fetch_sp500_tickers",     lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr("src.market_data.refresh.fetch_nasdaq100_tickers", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", lambda: ["AAPL", "AMZN"])
    monkeypatch.setattr("src.market_data.refresh.fetch_nyse_large_cap_tickers", lambda: [])

    # Stub OHLCV download to return empty (we only care about fundamentals here)
    monkeypatch.setattr("src.market_data.refresh._download_ohlcv_batch", lambda symbols, period="5d": {})

    # Stub fundamental fetch to return bare rows (no universes yet)
    def _mock_fundamentals(symbols):
        return [{"symbol": s, "market_cap_b": 10.0, "price": 100.0, "beta": 1.0, "iv_pct": None}
                for s in symbols]
    monkeypatch.setattr("src.market_data.refresh._fetch_fundamentals_batch", _mock_fundamentals)

    refresh_universe(full=False)

    all_rows = get_all_fundamentals()
    lookup = {r["symbol"]: r for r in all_rows}

    # AAPL: sp500 + nasdaq100 + nasdaq_large
    assert "nasdaq_large" in lookup["AAPL"]["universes"]
    assert "nasdaq100" in lookup["AAPL"]["universes"]
    assert "sp500" in lookup["AAPL"]["universes"]

    # MSFT: sp500 + nasdaq100 only
    assert "sp500" in lookup["MSFT"]["universes"]
    assert "nasdaq100" in lookup["MSFT"]["universes"]
    assert "nasdaq_large" not in lookup["MSFT"]["universes"]

    # AMZN: nasdaq_large only
    assert lookup["AMZN"]["universes"] == "nasdaq_large"


class TestRefreshUniverseEmptyGuard:
    def test_returns_error_dict_when_universe_empty(self):
        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
        ):
            result = refresh_universe(full=False)

        assert "error" in result
        assert result["error"] == "Empty universe"
        assert "elapsed_s" in result

    def test_no_db_writes_when_universe_empty(self):
        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.bulk_upsert_ohlcv_multi") as mock_ohlcv,
            patch("src.market_data.refresh.bulk_upsert_fundamentals") as mock_fund,
        ):
            refresh_universe(full=False)

        mock_ohlcv.assert_not_called()
        mock_fund.assert_not_called()


# ── refresh_universe — yf.download error handling ────────────────────────────

class TestRefreshUniverseDownloadErrorHandling:
    """An exception from yf.download() should not abort the whole refresh."""

    def test_download_exception_does_not_raise(self):
        """refresh_universe must complete without re-raising a download exception."""
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", side_effect=RuntimeError("network error")),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)  # must not raise

        assert "error" not in result
        assert result["ohlcv_rows_upserted"] == 0  # nothing was stored

    def test_download_exception_still_runs_fundamentals(self):
        """Even if OHLCV download fails, fundamentals should still be fetched."""

        def _ticker_factory(symbol):
            mock = MagicMock()
            mock.info = _make_ticker_info(symbol)
            return mock

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", side_effect=RuntimeError("network error")),
            patch("src.market_data.refresh.yf.Ticker", side_effect=_ticker_factory),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        assert result["fundamentals_upserted"] == 2

    def test_summary_dict_has_expected_keys(self):
        """Successful run returns summary with all expected keys."""
        multi_df = _make_multi_index_df(SMALL_UNIVERSE, n=3)
        ticker_mock = MagicMock()
        ticker_mock.info = _make_ticker_info("AAPL")

        with (
            patch("src.market_data.refresh.fetch_sp500_tickers", return_value=["AAPL", "MSFT"]),
            patch("src.market_data.refresh.fetch_nasdaq100_tickers", return_value=["GOOG"]),
            patch("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.fetch_nyse_large_cap_tickers", return_value=[]),
            patch("src.market_data.refresh.yf.download", return_value=multi_df),
            patch("src.market_data.refresh.yf.Ticker", return_value=ticker_mock),
            patch("src.market_data.refresh.time.sleep"),
        ):
            result = refresh_universe(full=False)

        expected_keys = {
            "universe_size",
            "ohlcv_rows_upserted",
            "fundamentals_upserted",
            "ohlcv_elapsed_s",
            "fundamentals_elapsed_s",
            "total_elapsed_s",
            "mode",
            "stuck_tickers",
            "store_status",
        }
        assert expected_keys.issubset(result.keys())


# ── refresh_universe — stuck-ticker visibility ───────────────────────────────

class TestRefreshUniverseStuckTickers:
    """A ticker still in the universe but never re-written should be surfaced."""

    def test_permanently_failing_ticker_is_reported_and_logged(self, monkeypatch, caplog):
        ensure_tables()

        # STUCKX stays in the universe (so prune won't remove it) but its
        # fundamentals fetch always yields nothing (simulating a delisted /
        # reclassified ticker skipped every run) — its ancient updated_at persists.
        mod = "src.market_data.refresh."
        monkeypatch.setattr(mod + "fetch_sp500_tickers", lambda: ["AAPL", "STUCKX"])
        monkeypatch.setattr(mod + "fetch_nasdaq100_tickers", lambda: [])
        monkeypatch.setattr(mod + "fetch_nasdaq_large_cap_tickers", lambda: [])
        monkeypatch.setattr(mod + "fetch_nyse_large_cap_tickers", lambda: [])
        monkeypatch.setattr(mod + "_download_ohlcv_batch", lambda symbols, period="5d": {})

        # Seed a stale STUCKX row (40 days old) directly.
        conn = sqlite3.connect(_tmp_db_path)
        try:
            ancient = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            conn.execute(
                "INSERT INTO universe_fundamentals (symbol, updated_at) VALUES (?, ?)"
                " ON CONFLICT(symbol) DO UPDATE SET updated_at = excluded.updated_at",
                ("STUCKX", ancient),
            )
            conn.commit()
        finally:
            conn.close()

        # Fundamentals fetch returns rows for everything EXCEPT STUCKX (it's skipped).
        def _mock_fundamentals(symbols):
            return [{"symbol": s, "market_cap_b": 10.0, "price": 100.0, "beta": 1.0, "iv_pct": None}
                    for s in symbols if s != "STUCKX"]
        monkeypatch.setattr(mod + "_fetch_fundamentals_batch", _mock_fundamentals)

        with caplog.at_level("WARNING"):
            result = refresh_universe(full=False)

        assert "STUCKX" in result["stuck_tickers"]
        assert "AAPL" not in result["stuck_tickers"]
        assert any("STUCKX" in rec.message for rec in caplog.records)
