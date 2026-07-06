from __future__ import annotations

import sqlite3
from pathlib import Path

from ..config import settings

_DDL = """
CREATE TABLE IF NOT EXISTS detective_options (
    date            TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    best_iv         REAL,
    best_volume     INTEGER,
    occ_symbol      TEXT,
    pcr_vol         REAL,
    pcr_oi          REAL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS detective_features (
    date                    TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    is_prime                INTEGER NOT NULL,
    close_price             REAL,
    volume                  INTEGER,
    rsi                     REAL,
    adx                     REAL,
    ema20                   REAL,
    ema50                   REAL,
    ema150                  REAL,
    ema200                  REAL,
    sma20                   REAL,
    sma50                   REAL,
    sma150                  REAL,
    sma200                  REAL,
    price_vs_ema20_pct      REAL,
    price_vs_ema50_pct      REAL,
    price_vs_ema150_pct     REAL,
    price_vs_ema200_pct     REAL,
    price_vs_sma20_pct      REAL,
    price_vs_sma50_pct      REAL,
    price_vs_sma150_pct     REAL,
    price_vs_sma200_pct     REAL,
    price_above_ema20       INTEGER,
    price_above_ema50       INTEGER,
    price_above_ema150      INTEGER,
    price_above_ema200      INTEGER,
    price_above_sma20       INTEGER,
    price_above_sma50       INTEGER,
    price_above_sma150      INTEGER,
    price_above_sma200      INTEGER,
    ema20_above_ema50       INTEGER,
    ema50_above_ema150      INTEGER,
    ema50_above_ema200      INTEGER,
    ema150_above_ema200     INTEGER,
    sma20_above_sma50       INTEGER,
    sma50_above_sma150      INTEGER,
    sma50_above_sma200      INTEGER,
    sma150_above_sma200     INTEGER,
    bb_upper                REAL,
    bb_middle               REAL,
    bb_lower                REAL,
    bb_pct_b                REAL,
    bb_width_pct            REAL,
    price_above_bb_middle   INTEGER,
    price_above_bb_upper    INTEGER,
    price_below_bb_lower    INTEGER,
    rv20                    REAL,
    atr_pct                 REAL,
    volume_ratio            REAL,
    roc20                   REAL,
    macd_histogram          REAL,
    pct_from_52wk_high      REAL,
    sector                  TEXT,
    market_cap_b            REAL,
    beta                    REAL,
    forward_pe              REAL,
    peg_ratio               REAL,
    revenue_growth          REAL,
    earnings_growth         REAL,
    debt_to_equity          REAL,
    dividend_yield          REAL,
    fcf                     REAL,
    computed_at             TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS detective_macro (
    date                TEXT PRIMARY KEY,
    vix_score           REAL,
    vix_direction       TEXT,
    market_posture      TEXT,
    composite_score     REAL,
    fear_greed_score    REAL,
    spy_above_ema50     INTEGER,
    spy_above_ema200    INTEGER,
    spy_rsi             REAL,
    top_sectors         TEXT
);
"""


def _get_connection() -> sqlite3.Connection:
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


_FUNDAMENTAL_COLUMNS = [
    ("market_cap_b", "REAL"),
    ("beta", "REAL"),
    ("forward_pe", "REAL"),
    ("peg_ratio", "REAL"),
    ("revenue_growth", "REAL"),
    ("earnings_growth", "REAL"),
    ("debt_to_equity", "REAL"),
    ("dividend_yield", "REAL"),
    ("fcf", "REAL"),
]

_OPTIONS_COLUMNS = [
    ("pcr_vol", "REAL"),
    ("pcr_oi", "REAL"),
]


def ensure_tables() -> None:
    conn = _get_connection()
    try:
        conn.executescript(_DDL)
        conn.commit()
        # Add any new columns to an existing table (idempotent)
        for col, col_type in _FUNDAMENTAL_COLUMNS:
            try:
                conn.execute(
                    f"ALTER TABLE detective_features ADD COLUMN {col} {col_type}"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
        for col, col_type in _OPTIONS_COLUMNS:
            try:
                conn.execute(
                    f"ALTER TABLE detective_options ADD COLUMN {col} {col_type}"
                )
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
    finally:
        conn.close()


def backfill_fundamentals() -> int:
    """Join universe_fundamentals into detective_features for all existing rows.

    Safe to call multiple times — overwrites with current fundamentals snapshot.
    Returns number of rows updated.
    """
    conn = _get_connection()
    try:
        conn.execute("""
            UPDATE detective_features
            SET market_cap_b    = f.market_cap_b,
                beta            = f.beta,
                forward_pe      = f.forward_pe,
                peg_ratio       = f.peg_ratio,
                revenue_growth  = f.revenue_growth,
                earnings_growth = f.earnings_growth,
                debt_to_equity  = f.debt_to_equity,
                dividend_yield  = f.dividend_yield,
                fcf             = f.fcf
            FROM universe_fundamentals f
            WHERE detective_features.ticker = f.symbol
        """)
        count = conn.execute(
            "SELECT changes() AS n"
        ).fetchone()["n"]
        conn.commit()
        return count
    finally:
        conn.close()


def get_computed_pairs() -> set[tuple[str, str]]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT date, ticker FROM detective_features").fetchall()
        return {(r["date"], r["ticker"]) for r in rows}
    finally:
        conn.close()


def upsert_feature_rows_bulk(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _get_connection()
    try:
        cols = list(rows[0].keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("date", "ticker"))
        conn.executemany(
            f"INSERT INTO detective_features ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(date, ticker) DO UPDATE SET {updates}",
            [list(r.values()) for r in rows],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def upsert_macro_row(row: dict) -> None:
    conn = _get_connection()
    try:
        cols = list(row.keys())
        placeholders = ", ".join("?" for _ in cols)
        col_names = ", ".join(cols)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c != "date")
        conn.execute(
            f"INSERT INTO detective_macro ({col_names}) VALUES ({placeholders}) "
            f"ON CONFLICT(date) DO UPDATE SET {updates}",
            list(row.values()),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_features() -> list[dict]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM detective_features").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_macro_for_date(date: str) -> dict | None:
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM detective_macro WHERE date = ?", (date,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def upsert_options_rows(rows: list[dict]) -> int:
    if not rows:
        return 0
    conn = _get_connection()
    try:
        conn.executemany(
            """
            INSERT INTO detective_options (date, ticker, best_iv, best_volume, occ_symbol, pcr_vol, pcr_oi)
            VALUES (:date, :ticker, :best_iv, :best_volume, :occ_symbol,
                    :pcr_vol, :pcr_oi)
            ON CONFLICT(date, ticker) DO UPDATE SET
                best_iv = excluded.best_iv,
                best_volume = excluded.best_volume,
                occ_symbol = excluded.occ_symbol,
                pcr_vol = COALESCE(excluded.pcr_vol, detective_options.pcr_vol),
                pcr_oi  = COALESCE(excluded.pcr_oi,  detective_options.pcr_oi)
            """,
            [
                {**r, "pcr_vol": r.get("pcr_vol"), "pcr_oi": r.get("pcr_oi")}
                for r in rows
            ],
        )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def get_options_index() -> dict[tuple[str, str], dict]:
    """Return {(date, ticker): {best_iv, best_volume, occ_symbol}} for all stored rows."""
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT * FROM detective_options").fetchall()
        return {(r["date"], r["ticker"]): dict(r) for r in rows}
    finally:
        conn.close()


def get_computed_options_pairs() -> set[tuple[str, str]]:
    conn = _get_connection()
    try:
        rows = conn.execute("SELECT date, ticker FROM detective_options").fetchall()
        return {(r["date"], r["ticker"]) for r in rows}
    finally:
        conn.close()


def get_feature_counts() -> dict:
    conn = _get_connection()
    try:
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_features"
        ).fetchone()["cnt"]
        prime = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_features WHERE is_prime = 1"
        ).fetchone()["cnt"]
        macro = conn.execute(
            "SELECT COUNT(*) as cnt FROM detective_macro"
        ).fetchone()["cnt"]
        return {"total": total, "prime": prime, "control": total - prime, "macro_dates": macro}
    finally:
        conn.close()
