from __future__ import annotations

import logging

import pandas as pd

from .store import _get_connection

logger = logging.getLogger(__name__)

_LOOKBACK_ROWS = 504


def get_control_tickers(
    date: str,
    exclude: set[str],
    market_cap_min: float = 3.0,
    price_min: float = 5.0,
) -> list[str]:
    """Return tickers present in OHLCV on date, passing fundamentals filter, not in exclude."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT o.symbol
            FROM universe_daily_ohlcv o
            JOIN universe_fundamentals f ON o.symbol = f.symbol
            WHERE o.date = ?
              AND f.market_cap_b >= ?
              AND f.price >= ?
            ORDER BY o.symbol
            """,
            (date, market_cap_min, price_min),
        ).fetchall()
        return [r["symbol"] for r in rows if r["symbol"] not in exclude]
    finally:
        conn.close()


def load_ohlcv_batch_for_date(
    tickers: list[str],
    as_of_date: str,
) -> dict[str, pd.DataFrame]:
    """Batch-load OHLCV for multiple tickers up to as_of_date in a single query.

    Returns {ticker: DataFrame} with ascending DatetimeIndex, at most _LOOKBACK_ROWS rows.
    Tickers with no data are omitted from the result.
    """
    if not tickers:
        return {}

    conn = _get_connection()
    try:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"""
            SELECT symbol, date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol IN ({placeholders})
              AND date <= ?
            ORDER BY symbol, date ASC
            """,
            (*tickers, as_of_date),
        ).fetchall()
    finally:
        conn.close()

    # Group into per-ticker lists, keep last _LOOKBACK_ROWS
    raw: dict[str, list[dict]] = {}
    for r in rows:
        raw.setdefault(r["symbol"], []).append({
            "Date": r["date"],
            "Open": r["open"],
            "High": r["high"],
            "Low": r["low"],
            "Close": r["close"],
            "Volume": r["volume"],
        })

    dfs: dict[str, pd.DataFrame] = {}
    for sym, records in raw.items():
        records = records[-_LOOKBACK_ROWS:]
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        dfs[sym] = df

    return dfs
