"""Live market overview fetcher — sectors, VIX, GEX, and breadth."""

from __future__ import annotations

import asyncio
import csv
import logging
from io import StringIO

import httpx
import yfinance as yf

from ..config import settings

logger = logging.getLogger(__name__)

GEX_CSV_URL = "https://squeezemetrics.com/monitor/static/DIX.csv"

SECTOR_ETFS = {
    "XLK": "Technology",
    "XLF": "Financials",
    "XLE": "Energy",
    "XLV": "Healthcare",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLU": "Utilities",
    "XLP": "Consumer Staples",
    "XLY": "Consumer Discretionary",
    "XLRE": "Real Estate",
    "XLC": "Communication Services",
}

DEFENSIVE = {"XLU", "XLP", "XLV", "XLRE"}
CYCLICAL  = {"XLK", "XLY", "XLF", "XLI", "XLE", "XLB", "XLC"}


def _pct_change(series, lookback: int) -> float | None:
    """Percentage change from `lookback` bars ago to the last bar.

    Returns None if insufficient data.
    """
    if len(series) < lookback + 1:
        return None
    prev = float(series.iloc[-(lookback + 1)])
    curr = float(series.iloc[-1])
    if prev == 0:
        return None
    return round((curr - prev) / prev * 100, 2)


def _gex_bucket(value_b: float) -> tuple[str, str]:
    """Return (label, bucket) for a GEX value in billions."""
    if value_b < 0:
        return "Negative — High volatility risk", "negative"
    if value_b < 3:
        return "Low Positive — Muted hedging", "low"
    if value_b < 7:
        return "Moderate Positive — Normal pinning", "moderate"
    if value_b < 12:
        return "High Positive — Strong pinning", "high"
    return "Extreme — Max pinning, expect low realized vol", "extreme"


def _gex_trend(current_b: float, avg_b: float) -> str:
    """Return Rising / Falling / Flat based on current vs 20d average."""
    if avg_b == 0:
        return "Flat"
    diff_pct = (current_b - avg_b) / abs(avg_b)
    if diff_pct > 0.1:
        return "Rising"
    if diff_pct < -0.1:
        return "Falling"
    return "Flat"


async def _fetch_sectors() -> dict:
    tickers = list(SECTOR_ETFS.keys())
    raw = await asyncio.to_thread(
        yf.download,
        " ".join(tickers),
        period="30d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
    result = {}
    for ticker, name in SECTOR_ETFS.items():
        try:
            closes = raw[ticker]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if closes.empty:
            continue
        result[ticker] = {
            "name": name,
            "pct_1d": _pct_change(closes, 1),
            "pct_1w": _pct_change(closes, 5),
            "pct_1m": _pct_change(closes, 21),
        }
    return result


async def _fetch_vix() -> dict:
    raw = await asyncio.to_thread(
        yf.download,
        "^VIX ^VIX3M",
        period="10d",
        group_by="ticker",
        progress=False,
        auto_adjust=True,
    )
    try:
        vix_closes = raw["^VIX"]["Close"].dropna()
        vix3m_closes = raw["^VIX3M"]["Close"].dropna()
    except KeyError as exc:
        raise ValueError(f"VIX data missing from download: {exc}") from exc
    if vix_closes.empty or vix3m_closes.empty:
        raise ValueError("VIX or VIX3M returned no data")

    spot = round(float(vix_closes.iloc[-1]), 2)
    vix3m_val = round(float(vix3m_closes.iloc[-1]), 2)
    spread = round(float(vix3m_closes.iloc[-1]) - float(vix_closes.iloc[-1]), 2)

    if spread > 0.5:
        term_structure = "Contango"
        stress_note = "normal, calm"
    elif spread < -0.5:
        term_structure = "Backwardation"
        stress_note = "elevated stress"
    else:
        term_structure = "Flat"
        stress_note = "transitioning"

    return {
        "spot": spot,
        "pct_1d": _pct_change(vix_closes, 1),
        "pct_1w": _pct_change(vix_closes, 5),
        "vix3m": vix3m_val,
        "term_structure": term_structure,
        "spread": spread,
        "stress_note": stress_note,
    }


async def _fetch_gex() -> dict:
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(GEX_CSV_URL)
        resp.raise_for_status()
        csv_text = resp.text

    rows = list(csv.reader(StringIO(csv_text)))
    data_rows = rows[1:]  # skip header
    if not data_rows:
        raise ValueError("GEX CSV is empty")

    gex_values: list[float] = []
    for row in data_rows:
        try:
            gex_values.append(float(row[3]))
        except (ValueError, IndexError):
            continue

    if not gex_values:
        raise ValueError("No GEX values parsed from CSV")

    current_b = round(gex_values[-1] / 1e9, 2)
    last_20 = gex_values[-20:]
    rolling_b = round((sum(last_20) / len(last_20)) / 1e9, 2)

    label, bucket = _gex_bucket(current_b)
    trend = _gex_trend(current_b, rolling_b)

    return {
        "value_b": current_b,
        "rolling_20d_avg_b": rolling_b,
        "trend": trend,
        "label": label,
        "bucket": bucket,
    }


def _fetch_breadth() -> dict | None:
    import sqlite3

    db_path = settings.db_path
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        tickers = [r[0] for r in conn.execute(
            "SELECT DISTINCT symbol FROM universe_daily_ohlcv ORDER BY symbol"
        ).fetchall()]

        qualifying = 0
        above = 0
        for ticker in tickers:
            rows = conn.execute(
                "SELECT close FROM universe_daily_ohlcv WHERE symbol = ? ORDER BY date ASC",
                (ticker,),
            ).fetchall()
            if len(rows) < 200:
                continue
            qualifying += 1
            closes = [r[0] for r in rows]
            ma200 = sum(closes[-200:]) / 200
            if closes[-1] > ma200:
                above += 1

        if qualifying < 50:
            logger.warning("Breadth: only %d tickers with 200d history, returning None", qualifying)
            return None

        pct_above = round(above / qualifying * 100, 1)

        latest_date = conn.execute(
            "SELECT MAX(date) FROM universe_daily_ohlcv"
        ).fetchone()[0]
        prev_rows = conn.execute(
            "SELECT DISTINCT date FROM universe_daily_ohlcv"
            " WHERE date < ? ORDER BY date DESC LIMIT 1",
            (latest_date,),
        ).fetchone()
        if not prev_rows:
            return {"pct_above_200ma": pct_above, "advancing": 0, "declining": 0, "ad_ratio": None}
        prev_date = prev_rows[0]

        today_prices = dict(conn.execute(
            "SELECT symbol, close FROM universe_daily_ohlcv WHERE date = ?",
            (latest_date,),
        ).fetchall())
        yesterday_prices = dict(conn.execute(
            "SELECT symbol, close FROM universe_daily_ohlcv WHERE date = ?",
            (prev_date,),
        ).fetchall())

        advancing = 0
        declining = 0
        for sym in today_prices:
            if sym not in yesterday_prices:
                continue
            if today_prices[sym] > yesterday_prices[sym]:
                advancing += 1
            elif today_prices[sym] < yesterday_prices[sym]:
                declining += 1

        ad_ratio = round(advancing / declining, 2) if declining > 0 else None

        return {
            "pct_above_200ma": pct_above,
            "advancing": advancing,
            "declining": declining,
            "ad_ratio": ad_ratio,
        }


async def fetch_market_overview() -> dict:
    raise NotImplementedError
