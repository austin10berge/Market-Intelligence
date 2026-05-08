"""SQLite-backed OHLCV + fundamentals store for the S&P 500 / NASDAQ 100 universe.

Tables
------
  universe_daily_ohlcv   — (symbol, date, open, high, low, close, volume)
  universe_fundamentals  — (symbol, market_cap_b, price, beta, iv_pct,
                            fcf, debt_to_equity, revenue_growth, earnings_growth,
                            dividend_yield, forward_pe, universes, updated_at)

All writes use INSERT … ON CONFLICT … DO UPDATE so they are safe to call
repeatedly without creating duplicates.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, date, timezone
from pathlib import Path

import pandas as pd

from ..config import settings

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """\
CREATE TABLE IF NOT EXISTS universe_daily_ohlcv (
    symbol   TEXT    NOT NULL,
    date     TEXT    NOT NULL,
    open     REAL    NOT NULL,
    high     REAL    NOT NULL,
    low      REAL    NOT NULL,
    close    REAL    NOT NULL,
    volume   INTEGER NOT NULL,
    PRIMARY KEY (symbol, date)
);

CREATE INDEX IF NOT EXISTS idx_ohlcv_symbol ON universe_daily_ohlcv(symbol);
CREATE INDEX IF NOT EXISTS idx_ohlcv_date   ON universe_daily_ohlcv(date);

CREATE TABLE IF NOT EXISTS universe_fundamentals (
    symbol          TEXT PRIMARY KEY,
    market_cap_b    REAL,
    price           REAL,
    beta            REAL,
    iv_pct          REAL,
    fcf             REAL,
    debt_to_equity  REAL,
    revenue_growth  REAL,
    earnings_growth REAL,
    dividend_yield  REAL,
    forward_pe      REAL,
    universes       TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL
);
"""


def _get_connection() -> sqlite3.Connection:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_NEW_FUNDAMENTAL_COLUMNS = [
    "fcf REAL",
    "debt_to_equity REAL",
    "revenue_growth REAL",
    "earnings_growth REAL",
    "dividend_yield REAL",
    "forward_pe REAL",
    "universes TEXT NOT NULL DEFAULT ''",
]


def ensure_tables() -> None:
    """Create the OHLCV and fundamentals tables if they don't exist."""
    conn = _get_connection()
    try:
        conn.executescript(_DDL)
        for col_def in _NEW_FUNDAMENTAL_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE universe_fundamentals ADD COLUMN {col_def}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        logger.info("Market data tables ensured")
    finally:
        conn.close()


# ── OHLCV writes ──────────────────────────────────────────────────────────────

def bulk_upsert_ohlcv(symbol: str, df: pd.DataFrame) -> int:
    """Upsert OHLCV rows for a single ticker from a yfinance-style DataFrame.

    Returns the number of rows upserted.
    """
    if df.empty:
        return 0

    conn = _get_connection()
    try:
        rows = []
        for idx, row in df.iterrows():
            # yfinance returns DatetimeIndex (possibly tz-aware)
            dt = idx.date() if hasattr(idx, "date") else str(idx)[:10]
            rows.append((
                symbol,
                str(dt),
                float(row["Open"]),
                float(row["High"]),
                float(row["Low"]),
                float(row["Close"]),
                int(row["Volume"]),
            ))

        conn.executemany(
            """
            INSERT INTO universe_daily_ohlcv (symbol, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open   = excluded.open,
                high   = excluded.high,
                low    = excluded.low,
                close  = excluded.close,
                volume = excluded.volume
            """,
            rows,
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def bulk_upsert_ohlcv_multi(data: dict[str, pd.DataFrame]) -> int:
    """Upsert OHLCV rows for multiple tickers at once (single transaction).

    Args:
        data: {symbol: DataFrame} mapping from yf.download() with group_by='ticker'.

    Returns total rows upserted.
    """
    conn = _get_connection()
    total = 0
    try:
        all_rows = []
        for symbol, df in data.items():
            if df.empty:
                continue
            for idx, row in df.iterrows():
                dt = idx.date() if hasattr(idx, "date") else str(idx)[:10]
                try:
                    all_rows.append((
                        symbol,
                        str(dt),
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"]),
                        int(row["Volume"]),
                    ))
                except (ValueError, KeyError):
                    continue

        conn.executemany(
            """
            INSERT INTO universe_daily_ohlcv (symbol, date, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, date) DO UPDATE SET
                open   = excluded.open,
                high   = excluded.high,
                low    = excluded.low,
                close  = excluded.close,
                volume = excluded.volume
            """,
            all_rows,
        )
        conn.commit()
        total = len(all_rows)
    finally:
        conn.close()

    return total


# ── OHLCV reads ───────────────────────────────────────────────────────────────

def get_ohlcv(symbol: str, lookback_days: int = 504) -> pd.DataFrame:
    """Return OHLCV data for a single ticker as a pandas DataFrame.

    Returns at most `lookback_days` most recent rows, sorted ascending by date.
    The returned DataFrame has columns: Open, High, Low, Close, Volume
    with a DatetimeIndex (tz-naive, like yfinance after tz_convert(None)).
    """
    conn = _get_connection()
    try:
        rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol = ?
            ORDER BY date DESC
            LIMIT ?
            """,
            (symbol, lookback_days),
        ).fetchall()

        if not rows:
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        records = [
            {
                "Date": r["date"],
                "Open": r["open"],
                "High": r["high"],
                "Low": r["low"],
                "Close": r["close"],
                "Volume": r["volume"],
            }
            for r in reversed(rows)  # ascending order
        ]
        df = pd.DataFrame(records)
        df["Date"] = pd.to_datetime(df["Date"])
        df.set_index("Date", inplace=True)
        return df
    finally:
        conn.close()


def get_ohlcv_batch(symbols: list[str], lookback_days: int = 504) -> dict[str, pd.DataFrame]:
    """Return OHLCV data for multiple tickers in a single DB connection.

    Returns {symbol: DataFrame} mapping. Missing symbols get an empty DataFrame.
    """
    conn = _get_connection()
    try:
        placeholders = ",".join("?" for _ in symbols)
        rows = conn.execute(
            f"""
            SELECT symbol, date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol IN ({placeholders})
              AND date >= (
                  SELECT date FROM universe_daily_ohlcv
                  WHERE symbol = (SELECT symbol FROM universe_daily_ohlcv LIMIT 1)
                  ORDER BY date DESC
                  LIMIT 1 OFFSET ?
              )
            ORDER BY symbol, date ASC
            """,
            (*symbols, lookback_days),
        ).fetchall()

        # Group by symbol
        result: dict[str, list] = {s: [] for s in symbols}
        for r in rows:
            result.setdefault(r["symbol"], []).append({
                "Date": r["date"],
                "Open": r["open"],
                "High": r["high"],
                "Low": r["low"],
                "Close": r["close"],
                "Volume": r["volume"],
            })

        dfs = {}
        for sym, records in result.items():
            if not records:
                dfs[sym] = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
                continue
            df = pd.DataFrame(records)
            df["Date"] = pd.to_datetime(df["Date"])
            df.set_index("Date", inplace=True)
            dfs[sym] = df

        return dfs
    finally:
        conn.close()


# ── Fundamentals writes ───────────────────────────────────────────────────────

def bulk_upsert_fundamentals(rows: list[dict]) -> int:
    """Upsert fundamental data rows.

    Each row dict should have: symbol, market_cap_b, price, beta, iv_pct,
    and optionally: fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield, forward_pe, universes.
    Returns the number of rows upserted.
    """
    if not rows:
        return 0

    conn = _get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        params = [
            (
                r["symbol"],
                r.get("market_cap_b"),
                r.get("price"),
                r.get("beta"),
                r.get("iv_pct"),
                r.get("fcf"),
                r.get("debt_to_equity"),
                r.get("revenue_growth"),
                r.get("earnings_growth"),
                r.get("dividend_yield"),
                r.get("forward_pe"),
                r.get("universes", ""),
                now,
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO universe_fundamentals
                (symbol, market_cap_b, price, beta, iv_pct,
                 fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                 forward_pe, universes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                market_cap_b    = excluded.market_cap_b,
                price           = excluded.price,
                beta            = excluded.beta,
                iv_pct          = excluded.iv_pct,
                fcf             = excluded.fcf,
                debt_to_equity  = excluded.debt_to_equity,
                revenue_growth  = excluded.revenue_growth,
                earnings_growth = excluded.earnings_growth,
                dividend_yield  = excluded.dividend_yield,
                forward_pe      = excluded.forward_pe,
                universes       = excluded.universes,
                updated_at      = excluded.updated_at
            """,
            params,
        )
        conn.commit()
        return len(params)
    finally:
        conn.close()


# ── Fundamentals reads ────────────────────────────────────────────────────────

def get_all_fundamentals() -> list[dict]:
    """Return all fundamental rows as a list of dicts."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            """SELECT symbol, market_cap_b, price, beta, iv_pct,
                      fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                      forward_pe, universes, updated_at
               FROM universe_fundamentals"""
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_fundamentals_for_tickers(tickers: list[str]) -> list[dict]:
    """Return fundamental rows for specific tickers."""
    if not tickers:
        return []
    conn = _get_connection()
    try:
        placeholders = ",".join("?" for _ in tickers)
        rows = conn.execute(
            f"""SELECT symbol, market_cap_b, price, beta, iv_pct,
                       fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                       forward_pe, universes, updated_at
                FROM universe_fundamentals WHERE symbol IN ({placeholders})""",
            tickers,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ── Store metadata ────────────────────────────────────────────────────────────

def get_store_status() -> dict:
    """Return metadata about the local data store for the API/UI.

    Returns:
        dict with keys: ohlcv_ticker_count, ohlcv_row_count, ohlcv_latest_date,
                        fundamentals_count, fundamentals_updated_at, is_stale
    """
    conn = _get_connection()
    try:
        # Ensure tables exist so queries don't fail on first call
        conn.executescript(_DDL)

        ohlcv_tickers = conn.execute(
            "SELECT COUNT(DISTINCT symbol) as cnt FROM universe_daily_ohlcv"
        ).fetchone()
        ohlcv_rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM universe_daily_ohlcv"
        ).fetchone()
        ohlcv_latest = conn.execute(
            "SELECT MAX(date) as latest FROM universe_daily_ohlcv"
        ).fetchone()

        fund_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM universe_fundamentals"
        ).fetchone()
        fund_updated = conn.execute(
            "SELECT MIN(updated_at) as oldest FROM universe_fundamentals"
        ).fetchone()

        latest_date = ohlcv_latest["latest"] if ohlcv_latest else None
        fund_updated_at = fund_updated["oldest"] if fund_updated else None

        # Staleness check: >48 hours since the oldest fundamental was updated
        is_stale = False
        stale_hours: float | None = None
        if fund_updated_at:
            try:
                updated_dt = datetime.fromisoformat(fund_updated_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600
                stale_hours = round(age_hours, 1)
                is_stale = age_hours > 48
            except Exception:
                is_stale = True

        return {
            "ohlcv_ticker_count": ohlcv_tickers["cnt"] if ohlcv_tickers else 0,
            "ohlcv_row_count": ohlcv_rows["cnt"] if ohlcv_rows else 0,
            "ohlcv_latest_date": latest_date,
            "fundamentals_count": fund_count["cnt"] if fund_count else 0,
            "fundamentals_updated_at": fund_updated_at,
            "is_stale": is_stale,
            "stale_hours": stale_hours,
        }
    finally:
        conn.close()


def get_universe_tickers() -> list[str]:
    """Return the list of tickers that have OHLCV data in the store."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT symbol FROM universe_daily_ohlcv ORDER BY symbol"
        ).fetchall()
        return [r["symbol"] for r in rows]
    finally:
        conn.close()


def prune_stale_fundamentals(current_universe: list[str]) -> int:
    """Delete fundamentals rows for tickers no longer in the current universe.

    Without this, removed tickers accumulate as zombie rows whose stale
    updated_at timestamps cause MIN(updated_at) to always appear old.

    Returns the number of rows deleted.
    """
    if not current_universe:
        return 0
    conn = _get_connection()
    try:
        placeholders = ",".join("?" * len(current_universe))
        cursor = conn.execute(
            f"DELETE FROM universe_fundamentals WHERE symbol NOT IN ({placeholders})",
            current_universe,
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()
