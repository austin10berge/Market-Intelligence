"""Backfill adr20_pct for existing detective_features rows.

Reads OHLCV from universe_daily_ohlcv, computes 20-day average daily range %,
and updates rows where adr20_pct IS NULL.

Run:
    docker compose run --rm pipeline python -m src.algo_detective.backfill_adr
"""

from __future__ import annotations

import logging
from collections import defaultdict

import pandas as pd

from ..indicators import compute_adr20_pct
from .store import _get_connection, ensure_tables
from .universe import load_ohlcv_batch_for_date

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_BATCH_SIZE = 100


def _compute_adr20(df: pd.DataFrame, as_of_date: str) -> float | None:
    cutoff = pd.Timestamp(as_of_date)
    df = df[df.index <= cutoff]
    if len(df) < 20:
        return None
    adr = compute_adr20_pct(df["High"].iloc[-20:], df["Low"].iloc[-20:], df["Close"].iloc[-20:])
    return round(adr, 4) if adr is not None else None


def run_backfill() -> None:
    ensure_tables()
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT date, ticker FROM detective_features WHERE adr20_pct IS NULL"
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        logger.info("No rows need backfill — adr20_pct already populated for all rows")
        return

    logger.info("Backfilling adr20_pct for %d rows", len(rows))

    # Group by date for efficient batch OHLCV loading
    by_date: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_date[r["date"]].append(r["ticker"])

    total_updated = 0
    for date_str, tickers in sorted(by_date.items()):
        logger.info("Processing date %s (%d tickers)", date_str, len(tickers))

        # Load in batches to avoid huge queries
        ohlcv_map: dict[str, pd.DataFrame] = {}
        for i in range(0, len(tickers), _BATCH_SIZE):
            batch = tickers[i : i + _BATCH_SIZE]
            ohlcv_map.update(load_ohlcv_batch_for_date(batch, date_str))

        updates: list[tuple[float | None, str, str]] = []
        for ticker in tickers:
            df = ohlcv_map.get(ticker)
            val = _compute_adr20(df, date_str) if df is not None else None
            updates.append((val, date_str, ticker))

        conn = _get_connection()
        try:
            conn.executemany(
                "UPDATE detective_features SET adr20_pct = ? WHERE date = ? AND ticker = ?",
                updates,
            )
            conn.commit()
            total_updated += len(updates)
        finally:
            conn.close()

    logger.info("Backfill complete: %d rows updated", total_updated)


if __name__ == "__main__":
    run_backfill()
