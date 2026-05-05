"""Live market overview fetcher — sectors, VIX, GEX, and breadth."""

from __future__ import annotations

import asyncio  # noqa: F401
import csv
import logging
import sqlite3  # noqa: F401
from contextlib import closing  # noqa: F401
from io import StringIO

import httpx
import yfinance as yf  # noqa: F401

from ..config import settings  # noqa: F401

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
    raise NotImplementedError


async def _fetch_vix() -> dict | None:
    raise NotImplementedError


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
    raise NotImplementedError


async def fetch_market_overview() -> dict:
    raise NotImplementedError
