"""Unit tests for src.market_data.store — OHLCV + fundamentals SQLite store.

Tests use a temporary SQLite database (in-memory isn't possible since the
module reads settings.db_path, so we patch it to a temp file).
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest

# Patch db_path BEFORE importing the module so ensure_tables() hits the temp DB
_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    """Redirect all store operations to a temp DB file."""
    with patch("src.market_data.store.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield
    # Don't delete between tests — tables persist for the session


@pytest.fixture(autouse=True, scope="session")
def _cleanup_db():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


from src.market_data.store import (
    ensure_tables,
    bulk_upsert_ohlcv,
    bulk_upsert_ohlcv_multi,
    bulk_upsert_fundamentals,
    get_ohlcv,
    get_all_fundamentals,
    get_fundamentals_for_tickers,
    get_stale_fundamental_tickers,
    get_store_status,
    get_universe_tickers,
)


def _set_updated_at(symbol: str, iso_ts: str) -> None:
    """Directly override a fundamentals row's updated_at (simulates a stale row)."""
    conn = sqlite3.connect(_tmp_db_path)
    try:
        conn.execute(
            "UPDATE universe_fundamentals SET updated_at = ? WHERE symbol = ?",
            (iso_ts, symbol),
        )
        conn.commit()
    finally:
        conn.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_ohlcv_df(n: int = 5, start_date: str = "2024-01-02") -> pd.DataFrame:
    """Build a small OHLCV DataFrame with a DatetimeIndex."""
    dates = pd.bdate_range(start=start_date, periods=n, tz="America/New_York")
    return pd.DataFrame(
        {
            "Open": [100 + i for i in range(n)],
            "High": [101 + i for i in range(n)],
            "Low": [99 + i for i in range(n)],
            "Close": [100.5 + i for i in range(n)],
            "Volume": [1_000_000] * n,
        },
        index=dates,
    )


# ── ensure_tables ─────────────────────────────────────────────────────────────

class TestEnsureTables:
    def test_creates_tables_without_error(self):
        ensure_tables()
        # Call again — should be idempotent
        ensure_tables()

    def test_status_returns_zeros_on_empty_db(self):
        ensure_tables()
        status = get_store_status()
        assert status["ohlcv_ticker_count"] == 0 or isinstance(status["ohlcv_ticker_count"], int)
        assert status["fundamentals_count"] == 0 or isinstance(status["fundamentals_count"], int)


# ── OHLCV upsert + read ──────────────────────────────────────────────────────

class TestOhlcvUpsert:
    def test_bulk_upsert_single_ticker(self):
        ensure_tables()
        df = _make_ohlcv_df(n=10)
        count = bulk_upsert_ohlcv("TEST", df)
        assert count == 10

    def test_read_back_matches(self):
        ensure_tables()
        df_in = _make_ohlcv_df(n=5, start_date="2024-06-01")
        bulk_upsert_ohlcv("READBACK", df_in)
        df_out = get_ohlcv("READBACK", lookback_days=10)
        assert len(df_out) == 5
        assert list(df_out.columns) == ["Open", "High", "Low", "Close", "Volume"]
        # Verify first row values
        assert df_out.iloc[0]["Close"] == pytest.approx(100.5)

    def test_upsert_overwrites_on_conflict(self):
        ensure_tables()
        df1 = _make_ohlcv_df(n=3, start_date="2024-07-01")
        bulk_upsert_ohlcv("UPSERT", df1)

        # Change the close price and re-upsert
        df2 = df1.copy()
        df2["Close"] = 999.0
        bulk_upsert_ohlcv("UPSERT", df2)

        df_out = get_ohlcv("UPSERT", lookback_days=10)
        assert len(df_out) == 3  # no duplicates
        assert df_out.iloc[0]["Close"] == pytest.approx(999.0)

    def test_empty_df_returns_zero(self):
        ensure_tables()
        empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        count = bulk_upsert_ohlcv("EMPTY", empty)
        assert count == 0

    def test_missing_ticker_returns_empty_df(self):
        ensure_tables()
        df = get_ohlcv("NONEXISTENT_TICKER_XYZ", lookback_days=10)
        assert df.empty
        assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


class TestOhlcvMultiUpsert:
    def test_multi_upsert(self):
        ensure_tables()
        data = {
            "AAA": _make_ohlcv_df(n=3, start_date="2024-08-01"),
            "BBB": _make_ohlcv_df(n=4, start_date="2024-08-01"),
        }
        total = bulk_upsert_ohlcv_multi(data)
        assert total == 7

    def test_multi_upsert_empty_dict(self):
        ensure_tables()
        total = bulk_upsert_ohlcv_multi({})
        assert total == 0


# ── Fundamentals upsert + read ────────────────────────────────────────────────

class TestFundamentals:
    def test_upsert_and_read(self):
        ensure_tables()
        rows = [
            {"symbol": "AAPL", "market_cap_b": 3000.0, "price": 190.0, "beta": 1.2, "iv_pct": 28.5},
            {"symbol": "MSFT", "market_cap_b": 2800.0, "price": 420.0, "beta": 0.9, "iv_pct": 22.0},
        ]
        count = bulk_upsert_fundamentals(rows)
        assert count == 2

        all_rows = get_all_fundamentals()
        symbols = {r["symbol"] for r in all_rows}
        assert "AAPL" in symbols
        assert "MSFT" in symbols

    def test_upsert_overwrites(self):
        ensure_tables()
        rows1 = [{"symbol": "GOOG", "market_cap_b": 1500.0, "price": 170.0, "beta": 1.1, "iv_pct": 30.0}]
        bulk_upsert_fundamentals(rows1)

        rows2 = [{"symbol": "GOOG", "market_cap_b": 1600.0, "price": 175.0, "beta": 1.1, "iv_pct": 32.0}]
        bulk_upsert_fundamentals(rows2)

        result = get_fundamentals_for_tickers(["GOOG"])
        assert len(result) == 1
        assert result[0]["market_cap_b"] == pytest.approx(1600.0)
        assert result[0]["iv_pct"] == pytest.approx(32.0)

    def test_empty_upsert(self):
        ensure_tables()
        count = bulk_upsert_fundamentals([])
        assert count == 0

    def test_get_for_missing_tickers(self):
        ensure_tables()
        result = get_fundamentals_for_tickers(["ZZZZZZ"])
        assert result == []


# ── Store status ──────────────────────────────────────────────────────────────

class TestStoreStatus:
    def test_status_has_expected_keys(self):
        ensure_tables()
        status = get_store_status()
        expected_keys = {
            "ohlcv_ticker_count", "ohlcv_row_count", "ohlcv_latest_date",
            "fundamentals_count", "fundamentals_updated_at", "is_stale", "stale_hours",
        }
        assert expected_keys.issubset(status.keys())

    def test_status_reflects_data(self):
        ensure_tables()
        # Insert some data
        bulk_upsert_ohlcv("STATUS_TEST", _make_ohlcv_df(n=5, start_date="2024-09-01"))
        bulk_upsert_fundamentals([
            {"symbol": "STATUS_TEST", "market_cap_b": 10.0, "price": 50.0, "beta": 1.0, "iv_pct": 25.0},
        ])

        status = get_store_status()
        assert status["ohlcv_ticker_count"] >= 1
        assert status["ohlcv_row_count"] >= 5
        assert status["fundamentals_count"] >= 1


# ── New fundamental columns ───────────────────────────────────────────────────

class TestFundamentalsNewColumns:
    def test_new_columns_upsert_and_read(self):
        ensure_tables()
        rows = [{
            "symbol": "NEWCOL",
            "market_cap_b": 50.0,
            "price": 100.0,
            "beta": 1.0,
            "iv_pct": 25.0,
            "fcf": 5.0,
            "debt_to_equity": 0.8,
            "revenue_growth": 0.12,
            "earnings_growth": 0.08,
            "dividend_yield": 0.015,
        }]
        count = bulk_upsert_fundamentals(rows)
        assert count == 1

        result = get_fundamentals_for_tickers(["NEWCOL"])
        assert len(result) == 1
        r = result[0]
        assert r["fcf"] == pytest.approx(5.0)
        assert r["debt_to_equity"] == pytest.approx(0.8)
        assert r["revenue_growth"] == pytest.approx(0.12)
        assert r["earnings_growth"] == pytest.approx(0.08)
        assert r["dividend_yield"] == pytest.approx(0.015)

    def test_new_columns_default_to_none_when_omitted(self):
        ensure_tables()
        rows = [{"symbol": "OLDSTYLE", "market_cap_b": 10.0, "price": 50.0, "beta": 1.0, "iv_pct": None}]
        bulk_upsert_fundamentals(rows)

        result = get_fundamentals_for_tickers(["OLDSTYLE"])
        assert len(result) == 1
        r = result[0]
        assert r["fcf"] is None
        assert r["debt_to_equity"] is None
        assert r["revenue_growth"] is None

    def test_ensure_tables_is_idempotent_with_migration(self):
        ensure_tables()
        ensure_tables()


# ── Universe tickers ──────────────────────────────────────────────────────────

class TestUniverseTickers:
    def test_returns_sorted_list(self):
        ensure_tables()
        bulk_upsert_ohlcv("ZZZ_TICKER", _make_ohlcv_df(n=2, start_date="2024-10-01"))
        bulk_upsert_ohlcv("AAA_TICKER", _make_ohlcv_df(n=2, start_date="2024-10-01"))
        tickers = get_universe_tickers()
        assert "AAA_TICKER" in tickers
        assert "ZZZ_TICKER" in tickers
        # Should be sorted
        aaa_idx = tickers.index("AAA_TICKER")
        zzz_idx = tickers.index("ZZZ_TICKER")
        assert aaa_idx < zzz_idx


# ── Universes column ──────────────────────────────────────────────────────────

class TestUniversesColumn:
    def test_universes_column_exists(self):
        """ensure_tables() must add the universes column to universe_fundamentals."""
        ensure_tables()
        conn = sqlite3.connect(_tmp_db_path)
        try:
            cols = [row[1] for row in conn.execute("PRAGMA table_info(universe_fundamentals)").fetchall()]
            assert "universes" in cols
        finally:
            conn.close()

    def test_bulk_upsert_fundamentals_stores_universes(self):
        ensure_tables()
        rows = [{"symbol": "ZVZZT", "market_cap_b": 5.0, "price": 50.0, "beta": 1.0,
                 "iv_pct": None, "universes": "sp500,nasdaq_large"}]
        n = bulk_upsert_fundamentals(rows)
        assert n == 1
        result = get_all_fundamentals()
        match = next((r for r in result if r["symbol"] == "ZVZZT"), None)
        assert match is not None
        assert match["universes"] == "sp500,nasdaq_large"

    def test_bulk_upsert_fundamentals_defaults_empty_universes(self):
        ensure_tables()
        rows = [{"symbol": "ZVZZT2", "market_cap_b": 3.0, "price": 30.0, "beta": 0.9, "iv_pct": None}]
        bulk_upsert_fundamentals(rows)
        result = get_all_fundamentals()
        match = next((r for r in result if r["symbol"] == "ZVZZT2"), None)
        assert match is not None
        assert match["universes"] == ""


# ── Staleness metric (MAX-based) ──────────────────────────────────────────────

class TestStalenessMetric:
    def test_one_ancient_row_does_not_pin_freshness(self):
        """A single very-old (stuck) row must NOT make the whole store look stale."""
        ensure_tables()
        # Many fresh rows (bulk_upsert stamps updated_at = now)
        fresh = [
            {"symbol": f"FRESH{i}", "market_cap_b": 10.0, "price": 50.0,
             "beta": 1.0, "iv_pct": None}
            for i in range(20)
        ]
        bulk_upsert_fundamentals(fresh)
        # One permanently-stuck row, 36 days old (the observed prod failure mode)
        bulk_upsert_fundamentals(
            [{"symbol": "STUCKROW", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None}]
        )
        ancient = (datetime.now(timezone.utc) - timedelta(days=36)).isoformat()
        _set_updated_at("STUCKROW", ancient)

        status = get_store_status()
        # MAX(updated_at) is fresh, so store is NOT stale despite the ancient row.
        assert status["is_stale"] is False
        assert status["stale_hours"] is not None
        assert status["stale_hours"] < 48


# ── get_stale_fundamental_tickers ─────────────────────────────────────────────

class TestStaleFundamentalTickers:
    def test_returns_only_rows_older_than_threshold(self):
        ensure_tables()
        bulk_upsert_fundamentals([
            {"symbol": "STALE_A", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None},
            {"symbol": "STALE_B", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None},
            {"symbol": "FRESH_C", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None},
        ])
        _set_updated_at("STALE_A", (datetime.now(timezone.utc) - timedelta(days=40)).isoformat())
        _set_updated_at("STALE_B", (datetime.now(timezone.utc) - timedelta(days=8)).isoformat())
        # FRESH_C keeps its now() timestamp

        symbols = [t["symbol"] for t in get_stale_fundamental_tickers(threshold_hours=168)]
        assert "STALE_A" in symbols
        assert "STALE_B" in symbols
        assert "FRESH_C" not in symbols

    def test_rows_include_updated_at(self):
        ensure_tables()
        bulk_upsert_fundamentals(
            [{"symbol": "STALE_TS", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None}]
        )
        ts = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        _set_updated_at("STALE_TS", ts)
        result = get_stale_fundamental_tickers(threshold_hours=168)
        match = next((t for t in result if t["symbol"] == "STALE_TS"), None)
        assert match is not None
        assert match["updated_at"] == ts

    def test_ordered_oldest_first(self):
        ensure_tables()
        bulk_upsert_fundamentals([
            {"symbol": "ORD_OLDER", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None},
            {"symbol": "ORD_NEWER", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None},
        ])
        _set_updated_at("ORD_OLDER", (datetime.now(timezone.utc) - timedelta(days=50)).isoformat())
        _set_updated_at("ORD_NEWER", (datetime.now(timezone.utc) - timedelta(days=9)).isoformat())
        symbols = [t["symbol"] for t in get_stale_fundamental_tickers(threshold_hours=168)]
        assert symbols.index("ORD_OLDER") < symbols.index("ORD_NEWER")

    def test_status_surfaces_stuck_tickers(self):
        ensure_tables()
        bulk_upsert_fundamentals(
            [{"symbol": "STUCK_ST", "market_cap_b": 1.0, "price": 5.0, "beta": 1.0, "iv_pct": None}]
        )
        old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
        _set_updated_at("STUCK_ST", old)
        status = get_store_status()
        assert "stuck_tickers" in status
        assert "STUCK_ST" in status["stuck_tickers"]
        assert len(status["stuck_tickers"]) <= 10
