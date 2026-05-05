"""Unit tests for src.fetchers.market_overview."""

from __future__ import annotations

import os
import sqlite3
import tempfile
from unittest.mock import patch

import httpx  # noqa: F401
import pandas as pd
import pytest
import respx  # noqa: F401

from src.fetchers.market_overview import (
    _fetch_breadth,  # noqa: F401
    _fetch_gex,  # noqa: F401
    _fetch_sectors,  # noqa: F401
    _fetch_vix,  # noqa: F401
    _gex_bucket,  # noqa: F401
    _gex_trend,  # noqa: F401
    fetch_market_overview,  # noqa: F401
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_yf_df(
    tickers: list[str],
    n_days: int,
    base: float = 100.0,
    step: float = 1.0,
) -> pd.DataFrame:
    """Build a fake yf.download multi-ticker DataFrame.

    close[i] = base + i * step for i in 0..n_days-1.
    All other columns are flat at base.
    """
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    data = {}
    for ticker in tickers:
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            if col == "Close":
                data[(ticker, col)] = [base + i * step for i in range(n_days)]
            elif col == "Volume":
                data[(ticker, col)] = [1_000_000] * n_days
            else:
                data[(ticker, col)] = [base] * n_days
    df = pd.DataFrame(data, index=dates)
    df.columns = pd.MultiIndex.from_tuples(list(data.keys()))
    return df


def _make_gex_csv(gex_values: list[float]) -> str:
    """Build a fake DIX.csv string."""
    lines = ["date,price,dix,gex"]
    for i, gex in enumerate(gex_values):
        lines.append(f"2024-01-{i+1:02d},400.0,0.45,{gex:.0f}")
    return "\n".join(lines)


_tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp_db_path = _tmp_db.name
_tmp_db.close()


@pytest.fixture(autouse=True)
def _patch_db_path():
    with patch("src.fetchers.market_overview.settings") as mock_settings:
        mock_settings.db_path = _tmp_db_path
        yield


@pytest.fixture(autouse=True, scope="session")
def _cleanup_db():
    yield
    try:
        os.unlink(_tmp_db_path)
    except OSError:
        pass


def _setup_breadth_db(
    tickers_ascending: list[str],
    tickers_descending: list[str],
    n_days: int = 210,
):
    """Populate the temp DB with OHLCV data for breadth tests."""
    conn = sqlite3.connect(_tmp_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS universe_daily_ohlcv (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL NOT NULL,
            high   REAL NOT NULL,
            low    REAL NOT NULL,
            close  REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    dates = pd.bdate_range(end="2024-01-31", periods=n_days)
    rows = []
    for sym in tickers_ascending:
        for i, d in enumerate(dates):
            close = 100.0 + i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    for sym in tickers_descending:
        for i, d in enumerate(dates):
            close = 400.0 - i
            rows.append((sym, str(d.date()), close, close, close, close, 1_000_000))
    conn.executemany(
        "INSERT OR REPLACE INTO universe_daily_ohlcv "
        "(symbol, date, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


# ── GEX pure helpers ──────────────────────────────────────────────────────────

def test_gex_bucket_negative():
    label, bucket = _gex_bucket(-1.0)
    assert bucket == "negative"
    assert "volatility" in label.lower()


def test_gex_bucket_low():
    label, bucket = _gex_bucket(1.5)
    assert bucket == "low"


def test_gex_bucket_moderate():
    label, bucket = _gex_bucket(5.0)
    assert bucket == "moderate"


def test_gex_bucket_high():
    label, bucket = _gex_bucket(9.0)
    assert bucket == "high"


def test_gex_bucket_extreme():
    label, bucket = _gex_bucket(15.0)
    assert bucket == "extreme"


def test_gex_bucket_boundaries():
    _, b0 = _gex_bucket(0.0)
    assert b0 == "low"        # exactly 0 = low (not negative)
    _, b3 = _gex_bucket(3.0)
    assert b3 == "moderate"   # exactly 3 = moderate
    _, b7 = _gex_bucket(7.0)
    assert b7 == "high"       # exactly 7 = high
    _, b12 = _gex_bucket(12.0)
    assert b12 == "extreme"   # exactly 12 = extreme


def test_gex_trend_rising():
    assert _gex_trend(6.0, 5.0) == "Rising"   # diff_pct = 0.2 > 0.1


def test_gex_trend_falling():
    assert _gex_trend(4.0, 5.0) == "Falling"  # diff_pct = -0.2 < -0.1


def test_gex_trend_flat():
    assert _gex_trend(5.0, 5.0) == "Flat"


def test_gex_trend_zero_avg():
    assert _gex_trend(5.0, 0.0) == "Flat"


def test_gex_trend_negative_regime():
    assert _gex_trend(-2.0, -5.0) == "Rising"   # less negative = improving
    assert _gex_trend(-8.0, -5.0) == "Falling"  # more negative = worsening


# ── GEX integration ───────────────────────────────────────────────────────────

@respx.mock
async def test_fetch_gex_values_and_trend():
    # 24 rows at 5B, then 1 row at 7B — last 20 = [5B x19, 7B] → avg = 5.1B
    gex_vals = [5_000_000_000] * 24 + [7_000_000_000]
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()

    assert result["value_b"] == 7.0
    assert result["rolling_20d_avg_b"] == pytest.approx(5.1, abs=0.01)
    assert result["trend"] == "Rising"
    assert result["bucket"] == "high"
    assert "Strong pinning" in result["label"]


@respx.mock
async def test_fetch_gex_bucket_negative_live():
    gex_vals = [-2_000_000_000] * 25
    respx.get(
        "https://squeezemetrics.com/monitor/static/DIX.csv"
    ).mock(return_value=httpx.Response(200, text=_make_gex_csv(gex_vals)))

    result = await _fetch_gex()
    assert result["bucket"] == "negative"
    assert result["trend"] == "Flat"


# ── Sectors ───────────────────────────────────────────────────────────────────

class TestSectors:
    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_returns_all_tickers(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK", "XLF"], n_days=30)
        etfs = {"XLK": "Technology", "XLF": "Financials"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result = await _fetch_sectors()
        assert set(result.keys()) == {"XLK", "XLF"}

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_pct_values(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=30, base=100.0, step=1.0)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result = await _fetch_sectors()
        xlk = result["XLK"]
        # close[-1] = 100 + 29 = 129, close[-2] = 128 → 1D = (129-128)/128*100 ≈ 0.78%
        assert xlk["pct_1d"] == pytest.approx(0.78, abs=0.01)
        # close[-1] = 129, close[-6] = 124 → 1W = (129-124)/124*100 ≈ 4.03%
        assert xlk["pct_1w"] == pytest.approx(4.03, abs=0.01)
        # close[-1] = 129, close[-22] = 108 → 1M = (129-108)/108*100 ≈ 19.44%
        assert xlk["pct_1m"] == pytest.approx(19.44, abs=0.01)

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_name_field(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=30)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result = await _fetch_sectors()
        assert result["XLK"]["name"] == "Technology"

    @patch("src.fetchers.market_overview.yf.download")
    async def test_sectors_null_when_insufficient_data(self, mock_dl):
        mock_dl.return_value = _make_yf_df(["XLK"], n_days=3)
        etfs = {"XLK": "Technology"}
        with patch.dict("src.fetchers.market_overview.SECTOR_ETFS", etfs, clear=True):
            result = await _fetch_sectors()
        # 3 rows → 1D works, 1W/1M are None (need 6 and 22 rows respectively)
        assert result["XLK"]["pct_1d"] is not None
        assert result["XLK"]["pct_1w"] is None
        assert result["XLK"]["pct_1m"] is None
