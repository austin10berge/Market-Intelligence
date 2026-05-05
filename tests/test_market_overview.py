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
