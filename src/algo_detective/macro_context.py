from __future__ import annotations

import json
import logging

import pandas as pd
import pandas_ta as ta

from .features import _last
from .store import _get_connection

logger = logging.getLogger(__name__)


def compute_macro_for_date(date: str) -> dict | None:
    """Build macro context row for a date.

    Pulls VIX + fear_greed from daily_signals, posture from digests,
    and SPY indicators from universe_daily_ohlcv. Returns None only
    if SPY OHLCV is missing; pipeline signal absence is handled gracefully.
    """
    conn = _get_connection()
    try:
        # ── Pipeline signals ──────────────────────────────────────────────────
        vix_row = conn.execute(
            "SELECT raw_value, direction, metadata FROM daily_signals "
            "WHERE date = ? AND source = 'vix'",
            (date,),
        ).fetchone()

        fg_row = conn.execute(
            "SELECT raw_value FROM daily_signals "
            "WHERE date = ? AND source = 'fear_greed'",
            (date,),
        ).fetchone()

        digest_row = conn.execute(
            "SELECT composite_score, posture FROM digests WHERE date = ?",
            (date,),
        ).fetchone()

        sector_rows = conn.execute(
            "SELECT metadata FROM daily_signals "
            "WHERE date = ? AND source = 'sector_etf'",
            (date,),
        ).fetchone()

        # ── SPY from OHLCV ────────────────────────────────────────────────────
        spy_rows = conn.execute(
            """
            SELECT date, open, high, low, close, volume
            FROM universe_daily_ohlcv
            WHERE symbol = 'SPY' AND date <= ?
            ORDER BY date ASC
            """,
            (date,),
        ).fetchall()
        spy_rows = spy_rows[-504:]
    finally:
        conn.close()

    if not spy_rows:
        logger.warning("No SPY OHLCV data up to %s — skipping macro row", date)
        return None

    spy_df = pd.DataFrame(
        [{"Date": r["date"], "Close": r["close"]} for r in spy_rows]
    )
    spy_df["Date"] = pd.to_datetime(spy_df["Date"])
    spy_df.set_index("Date", inplace=True)

    spy_close = spy_df["Close"]
    spy_ema50 = _last(ta.ema(spy_close, length=50))
    spy_ema200 = _last(ta.ema(spy_close, length=200))
    spy_rsi = _last(ta.rsi(spy_close, length=14))
    curr_spy = float(spy_close.iloc[-1])

    # ── Top sectors ───────────────────────────────────────────────────────────
    top_sectors: list[str] = []
    if sector_rows:
        try:
            meta = json.loads(sector_rows["metadata"])
            top_sectors = meta.get("top_sectors", [])
        except (json.JSONDecodeError, KeyError):
            pass

    return {
        "date": date,
        "vix_score": float(vix_row["raw_value"]) if vix_row else None,
        "vix_direction": vix_row["direction"] if vix_row else None,
        "market_posture": digest_row["posture"] if digest_row else None,
        "composite_score": float(digest_row["composite_score"]) if digest_row else None,
        "fear_greed_score": float(fg_row["raw_value"]) if fg_row else None,
        "spy_above_ema50": int(curr_spy > spy_ema50) if spy_ema50 is not None else None,
        "spy_above_ema200": int(curr_spy > spy_ema200) if spy_ema200 is not None else None,
        "spy_rsi": round(spy_rsi, 2) if spy_rsi is not None else None,
        "top_sectors": json.dumps(top_sectors),
    }
