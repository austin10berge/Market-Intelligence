"""SQLite-backed OHLCV + fundamentals store for the S&P 500 / NASDAQ 100 universe.

Tables
------
  universe_daily_ohlcv   — (symbol, date, open, high, low, close, volume)
  universe_fundamentals  — (symbol, market_cap_b, price, beta, iv_pct,
                            fcf, debt_to_equity, revenue_growth, earnings_growth,
                            dividend_yield, forward_pe, peg_ratio, universes, updated_at)

All writes use INSERT … ON CONFLICT … DO UPDATE so they are safe to call
repeatedly without creating duplicates.
"""

from __future__ import annotations

import logging
import math
import sqlite3
from datetime import datetime, timedelta, timezone
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
    peg_ratio       REAL,
    universes       TEXT NOT NULL DEFAULT '',
    sector          TEXT,
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
    "peg_ratio REAL",
    "universes TEXT NOT NULL DEFAULT ''",
    "sector TEXT",
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
                    o, h, l, c, v = (
                        float(row["Open"]),
                        float(row["High"]),
                        float(row["Low"]),
                        float(row["Close"]),
                        int(row["Volume"]),
                    )
                    # yfinance returns NaN for missing sessions; sqlite3 converts
                    # NaN to NULL which violates the NOT NULL constraint.
                    if any(math.isnan(x) for x in (o, h, l, c)):
                        continue
                    all_rows.append((symbol, str(dt), o, h, l, c, v))
                except (ValueError, KeyError, TypeError):
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
    and optionally: fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield, forward_pe, peg_ratio, universes, sector.
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
                r.get("peg_ratio"),
                r.get("universes", ""),
                r.get("sector"),
                now,
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO universe_fundamentals
                (symbol, market_cap_b, price, beta, iv_pct,
                 fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                 forward_pe, peg_ratio, universes, sector, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                peg_ratio       = excluded.peg_ratio,
                universes       = excluded.universes,
                sector          = excluded.sector,
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
                      forward_pe, peg_ratio, universes, sector, updated_at
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
                       forward_pe, peg_ratio, universes, sector, updated_at
                FROM universe_fundamentals WHERE symbol IN ({placeholders})""",
            tickers,
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_available_sectors() -> list[str]:
    """Return distinct non-null sectors from universe_fundamentals, sorted."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT DISTINCT sector FROM universe_fundamentals WHERE sector IS NOT NULL ORDER BY sector"
        ).fetchall()
        return [r["sector"] for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


# ── Store metadata ────────────────────────────────────────────────────────────

# Age (hours) past which the store's freshest fundamentals write is considered stale.
_STALE_THRESHOLD_HOURS = 48

# Age (hours) past which an individual ticker's row is treated as permanently
# stuck (never re-written) and surfaced in the status payload. 7 days.
_STUCK_THRESHOLD_HOURS = 168


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
        # Use MAX(updated_at) — the most recent successful write — as the freshness
        # signal, NOT MIN. A handful of permanently-failing tickers (delisted /
        # reclassified / renamed) never get re-written, so their ancient updated_at
        # would pin MIN forever and make the whole store look stale even when ~all
        # other tickers refreshed today. See get_stale_fundamental_tickers() for a
        # way to surface those stuck rows.
        fund_updated = conn.execute(
            "SELECT MAX(updated_at) as newest FROM universe_fundamentals"
        ).fetchone()

        latest_date = ohlcv_latest["latest"] if ohlcv_latest else None
        fund_updated_at = fund_updated["newest"] if fund_updated else None

        is_stale = False
        stale_hours: float | None = None
        if fund_updated_at:
            try:
                updated_dt = datetime.fromisoformat(fund_updated_at.replace("Z", "+00:00"))
                age_hours = (datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600
                stale_hours = round(age_hours, 1)
                is_stale = age_hours > _STALE_THRESHOLD_HOURS
            except Exception:
                is_stale = True

        # Surface up to 10 permanently-stuck tickers (older than 7 days) so the
        # "one zombie row" case is visible in the API/UI without log-diving.
        stuck_cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=_STUCK_THRESHOLD_HOURS)
        ).isoformat()
        stuck_rows = conn.execute(
            "SELECT symbol FROM universe_fundamentals"
            " WHERE updated_at < ? ORDER BY updated_at ASC LIMIT 10",
            (stuck_cutoff,),
        ).fetchall()
        stuck_tickers = [r["symbol"] for r in stuck_rows]

        status = {
            "ohlcv_ticker_count": ohlcv_tickers["cnt"] if ohlcv_tickers else 0,
            "ohlcv_row_count": ohlcv_rows["cnt"] if ohlcv_rows else 0,
            "ohlcv_latest_date": latest_date,
            "fundamentals_count": fund_count["cnt"] if fund_count else 0,
            "fundamentals_updated_at": fund_updated_at,
            "is_stale": is_stale,
            "stale_hours": stale_hours,
        }
        if stuck_tickers:
            status["stuck_tickers"] = stuck_tickers
        return status
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


def get_stale_fundamental_tickers(threshold_hours: int = 168) -> list[dict]:
    """Return fundamentals rows whose updated_at is older than threshold_hours.

    These are tickers that keep failing their yfinance fetch every refresh run
    (e.g. delisted, renamed, or reclassified so they no longer pass the
    quoteType == "EQUITY" check) yet remain nominally in the universe, so
    prune_stale_fundamentals() never removes them. Surfacing them makes the
    "one stuck row" problem diagnosable without a live production investigation.

    Args:
        threshold_hours: age cutoff; rows older than this are returned. Default
            168h (7 days).

    Returns:
        list of {"symbol": str, "updated_at": str}, oldest first.
    """
    # Compute the cutoff in Python so it matches the stored ISO-8601 format
    # (T separator, +00:00 offset) exactly — a lexicographic string compare then
    # equals a chronological compare, avoiding SQLite's space-separated
    # datetime('now') format mismatching at same-day boundaries.
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=threshold_hours)).isoformat()
    conn = _get_connection()
    try:
        conn.executescript(_DDL)
        rows = conn.execute(
            "SELECT symbol, updated_at FROM universe_fundamentals"
            " WHERE updated_at < ?"
            " ORDER BY updated_at ASC",
            (cutoff,),
        ).fetchall()
        return [{"symbol": r["symbol"], "updated_at": r["updated_at"]} for r in rows]
    finally:
        conn.close()
