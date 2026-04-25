"""Historical OHLCV data provider with SQLite caching.

Fetches data from yfinance and caches it locally so repeated backtests
on the same ticker don't re-download. The most recent 5 trading days
of cached data are refreshed on each fetch to pick up any corrections.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

from ..config import settings

logger = logging.getLogger(__name__)

# How many recent days of cache to consider stale and re-fetch
CACHE_STALE_DAYS = 5


def _get_cache_connection() -> sqlite3.Connection:
    """Get a SQLite connection for the price cache."""
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS backtest_price_cache (
            symbol TEXT NOT NULL,
            date TEXT NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume INTEGER NOT NULL,
            PRIMARY KEY (symbol, date)
        )
    """)
    conn.commit()
    return conn


def get_historical_data(
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
    timeframe: str = "daily",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Fetch historical OHLCV data for a ticker.

    Args:
        symbol: Ticker symbol (e.g. "AAPL", "SPY").
        start_date: Earliest date to include. None = go back as far as possible (~20yr).
        end_date: Latest date to include. None = today.
        timeframe: "daily" or "weekly".
        use_cache: Whether to use SQLite cache.

    Returns:
        DataFrame with columns: Date (index), Open, High, Low, Close, Volume.
        Sorted by date ascending. Empty DataFrame if no data found.
    """
    if end_date is None:
        end_date = date.today()
    if start_date is None:
        start_date = end_date - timedelta(days=365 * 22)  # ~22 years to ensure 20yr coverage

    symbol = symbol.upper().strip()

    if use_cache:
        df = _fetch_with_cache(symbol, start_date, end_date)
    else:
        df = _fetch_from_yfinance(symbol, start_date, end_date)

    if df.empty:
        return df

    # Filter to requested date range
    df = df.loc[
        (df.index >= pd.Timestamp(start_date)) &
        (df.index <= pd.Timestamp(end_date))
    ]

    # Aggregate to weekly if requested
    if timeframe == "weekly" and not df.empty:
        df = _aggregate_weekly(df)

    return df


def _fetch_with_cache(
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Try cache first, fill gaps from yfinance."""
    conn = _get_cache_connection()

    try:
        # Check what we have cached
        cached_range = conn.execute(
            "SELECT MIN(date) as min_d, MAX(date) as max_d FROM backtest_price_cache WHERE symbol = ?",
            (symbol,),
        ).fetchone()

        cached_min = cached_range["min_d"] if cached_range["min_d"] else None
        cached_max = cached_range["max_d"] if cached_range["max_d"] else None

        need_fetch = False
        fetch_start = start_date
        fetch_end = end_date

        if cached_min is None:
            # No cache at all
            need_fetch = True
        else:
            cached_min_date = datetime.strptime(cached_min, "%Y-%m-%d").date()
            cached_max_date = datetime.strptime(cached_max, "%Y-%m-%d").date()

            # Need data before our cache start?
            if start_date < cached_min_date:
                need_fetch = True
                fetch_start = start_date

            # Need data after our cache end, or cache is stale?
            if end_date > cached_max_date - timedelta(days=CACHE_STALE_DAYS):
                need_fetch = True
                if not (start_date < cached_min_date):
                    # Only fetch the gap + stale region
                    fetch_start = cached_max_date - timedelta(days=CACHE_STALE_DAYS)
                fetch_end = end_date

        if need_fetch:
            fresh_df = _fetch_from_yfinance(symbol, fetch_start, fetch_end)
            if not fresh_df.empty:
                _store_in_cache(conn, symbol, fresh_df)

        # Read full range from cache
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM backtest_price_cache
            WHERE symbol = ? AND date >= ? AND date <= ?
            ORDER BY date ASC
            """,
            (symbol, start_date.isoformat(), end_date.isoformat()),
        ).fetchall()

        if not rows:
            return pd.DataFrame()

        data = [{
            "Date": datetime.strptime(r["date"], "%Y-%m-%d"),
            "Open": r["open"],
            "High": r["high"],
            "Low": r["low"],
            "Close": r["close"],
            "Volume": r["volume"],
        } for r in rows]

        df = pd.DataFrame(data)
        df.set_index("Date", inplace=True)
        df.index = pd.DatetimeIndex(df.index)
        return df

    finally:
        conn.close()


def _fetch_from_yfinance(
    symbol: str,
    start_date: date,
    end_date: date,
) -> pd.DataFrame:
    """Download OHLCV from yfinance."""
    logger.info("Fetching %s from yfinance: %s → %s", symbol, start_date, end_date)

    try:
        ticker = yf.Ticker(symbol)
        # yfinance end date is exclusive, so add 1 day
        hist = ticker.history(
            start=start_date.isoformat(),
            end=(end_date + timedelta(days=1)).isoformat(),
            auto_adjust=True,
        )

        if hist.empty:
            logger.warning("No data returned from yfinance for %s", symbol)
            return pd.DataFrame()

        # Normalize index — remove timezone info
        hist = hist.copy()
        hist.index = pd.to_datetime(hist.index).tz_localize(None)

        # Keep only OHLCV columns
        cols_map = {}
        for col in hist.columns:
            col_lower = col.lower() if isinstance(col, str) else str(col).lower()
            if "open" in col_lower:
                cols_map[col] = "Open"
            elif "high" in col_lower:
                cols_map[col] = "High"
            elif "low" in col_lower:
                cols_map[col] = "Low"
            elif "close" in col_lower:
                cols_map[col] = "Close"
            elif "volume" in col_lower:
                cols_map[col] = "Volume"

        hist = hist.rename(columns=cols_map)
        required = ["Open", "High", "Low", "Close", "Volume"]
        for col in required:
            if col not in hist.columns:
                logger.warning("Missing column '%s' in yfinance data for %s", col, symbol)
                return pd.DataFrame()

        return hist[required].copy()

    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", symbol, exc)
        return pd.DataFrame()


def _store_in_cache(
    conn: sqlite3.Connection,
    symbol: str,
    df: pd.DataFrame,
) -> None:
    """Upsert OHLCV rows into the cache table."""
    rows = []
    for idx, row in df.iterrows():
        date_str = idx.strftime("%Y-%m-%d") if hasattr(idx, "strftime") else str(idx)[:10]
        rows.append((
            symbol,
            date_str,
            float(row["Open"]),
            float(row["High"]),
            float(row["Low"]),
            float(row["Close"]),
            int(row["Volume"]),
        ))

    conn.executemany(
        """
        INSERT INTO backtest_price_cache (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, date) DO UPDATE SET
            open = excluded.open,
            high = excluded.high,
            low = excluded.low,
            close = excluded.close,
            volume = excluded.volume
        """,
        rows,
    )
    conn.commit()
    logger.info("Cached %d bars for %s", len(rows), symbol)


def _aggregate_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily OHLCV to weekly bars."""
    weekly = df.resample("W-FRI").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum",
    }).dropna()
    return weekly
