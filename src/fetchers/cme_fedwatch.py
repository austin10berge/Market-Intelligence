"""CME FedWatch rate-expectations fetcher.

Derives the market-implied Federal Funds rate from the front-month 30-Day
Federal Funds futures contract (ZQ=F via yfinance) and compares it to the
current FFTR upper bound from FRED to estimate the expected rate change.

Signal value = implied rate change in percentage points (positive = cuts expected).
A value of +0.5 means 50bps of cuts are priced in; -0.25 means 25bps hike expected.
"""

from __future__ import annotations

import asyncio
import logging

import httpx
import yfinance as yf

from ..config import settings
from ..models import Signal, SignalSource
from .base import BaseFetcher
from .fred import fetch_fred_series

logger = logging.getLogger(__name__)

_FUTURES_TICKER = "ZQ=F"  # Front-month 30-Day Federal Funds futures


async def _get_current_fftr() -> float | None:
    """Fetch current Fed Funds Target Rate upper bound from FRED."""
    if not settings.fred_api_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            rate = await fetch_fred_series(client, "DFEDTARU")
        return rate
    except Exception as exc:
        logger.debug("FedWatch: FRED DFEDTARU fetch failed — %s", exc)
        return None


async def _get_futures_implied_rate() -> float | None:
    """Fetch front-month 30-Day FF futures price and return implied rate."""
    try:
        data = await asyncio.to_thread(
            yf.download,
            _FUTURES_TICKER,
            period="5d",
            progress=False,
            auto_adjust=True,
        )
        if data.empty:
            return None
        # Handle multi-level columns when a single ticker is downloaded
        if hasattr(data.columns, "levels"):
            try:
                closes = data[_FUTURES_TICKER]["Close"].dropna()
            except KeyError:
                closes = data["Close"].dropna()
        else:
            closes = data["Close"].dropna()

        if closes.empty:
            return None
        price = float(closes.iloc[-1])
        # Price convention: 100 - annualised implied rate
        implied_rate = round(100.0 - price, 4)
        logger.debug("FedWatch: ZQ=F price %.4f → implied rate %.3f%%", price, implied_rate)
        return implied_rate
    except Exception as exc:
        logger.debug("FedWatch: futures fetch failed — %s", exc)
        return None


class CmeFedWatchFetcher(BaseFetcher):
    """Fetch Fed rate expectations via 30-Day Fed Funds futures + current FFTR."""

    @property
    def name(self) -> str:
        return "CME FedWatch"

    async def fetch(self) -> Signal | None:
        current_rate, implied_rate = await asyncio.gather(
            _get_current_fftr(),
            _get_futures_implied_rate(),
        )

        if implied_rate is None:
            logger.warning("FedWatch: could not determine implied rate — skipping")
            return None

        # Fallback: assume 5.50% if FRED call failed (last known FFTR upper)
        if current_rate is None:
            logger.debug("FedWatch: FRED unavailable — using 5.50%% fallback for FFTR")
            current_rate = 5.50

        implied_change = round(current_rate - implied_rate, 3)
        expected_cuts = implied_change / 0.25  # each cut = 25bps

        if implied_change > 0.5:
            direction_label = f"{expected_cuts:.1f} cuts priced in"
        elif implied_change > 0:
            direction_label = f"~{implied_change * 100:.0f}bps of cuts priced in"
        elif implied_change < -0.25:
            hikes = abs(expected_cuts)
            direction_label = f"{hikes:.1f} hike(s) priced in"
        else:
            direction_label = "hold expected"

        summary = (
            f"FedWatch: FFTR {current_rate:.2f}% | Implied {implied_rate:.2f}% "
            f"({implied_change:+.2f}%) — {direction_label}"
        )

        return Signal(
            source=SignalSource.CME_FEDWATCH,
            value=implied_change,
            metadata={
                "current_rate": current_rate,
                "implied_rate": implied_rate,
                "implied_change": implied_change,
                "expected_cuts": round(expected_cuts, 2),
                "direction_label": direction_label,
            },
            summary=summary,
        )
