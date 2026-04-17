"""CBOE equity put/call ratio fetcher."""

from __future__ import annotations

import logging

from ..models import Signal, SignalSource
from .base import BaseFetcher, get_http_client
from .. import db

logger = logging.getLogger(__name__)

# CBOE publishes daily equity put/call ratio
CBOE_PC_URL = "https://www.cboe.com/us/options/market_statistics/daily/"


class PutCallFetcher(BaseFetcher):
    """Fetch CBOE equity put/call ratio with rolling average context."""

    @property
    def name(self) -> str:
        return "Put/Call Ratio"

    async def fetch(self) -> Signal | None:
        client = await get_http_client()

        # Try the CBOE market statistics page — we'll parse the equity P/C ratio
        # The CBOE website structure can change, so we have a fallback approach
        ratio = await self._fetch_from_cboe(client)

        if ratio is None:
            # Fallback: try via yfinance or alternative source
            ratio = await self._fetch_fallback()

        if ratio is None:
            logger.warning("Put/Call: unable to retrieve ratio from any source")
            return None

        ratio = round(ratio, 3)

        # Get 5-day rolling average from DB history
        rolling_avg = db.get_rolling_average(SignalSource.PUT_CALL.value, days=5)
        rolling_str = f" | 5d avg: {rolling_avg:.3f}" if rolling_avg else ""

        # Flag extremes
        if ratio > 1.2:
            extreme_note = " (elevated — near contrarian buy zone)"
        elif ratio < 0.7:
            extreme_note = " (low — excessive complacency)"
        else:
            extreme_note = ""

        return Signal(
            source=SignalSource.PUT_CALL,
            value=ratio,
            metadata={
                "rolling_avg_5d": rolling_avg,
                "extreme_high": ratio > 1.2,
                "extreme_low": ratio < 0.7,
            },
            summary=f"Put/Call: {ratio}{extreme_note}{rolling_str}",
        )

    async def _fetch_from_cboe(self, client: httpx.AsyncClient) -> float | None:
        """Attempt to fetch equity P/C ratio from CBOE market statistics."""
        try:
            resp = await client.get(CBOE_PC_URL)
            resp.raise_for_status()
            text = resp.text

            # Look for the equity put/call ratio in the page content
            # This is fragile and may need updating if CBOE changes their page
            import re

            # Pattern: look for equity total put/call ratio value
            # CBOE typically shows it as a decimal like 0.85
            patterns = [
                r"equity.*?put.*?call.*?ratio.*?([\d]+\.[\d]+)",
                r"total.*?put/call.*?([\d]+\.[\d]+)",
                r"put/call.*?ratio.*?([\d]+\.[\d]+)",
            ]

            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
                if match:
                    return float(match.group(1))

            logger.debug("Put/Call: could not parse CBOE page")
            return None
        except Exception:
            logger.debug("Put/Call: CBOE fetch failed", exc_info=True)
            return None

    async def _fetch_fallback(self) -> float | None:
        """Fallback: derive put/call ratio from options volume data."""
        try:
            import yfinance as yf

            spy = yf.Ticker("SPY")
            # Get the nearest expiry options chain
            if not spy.options:
                return None
            nearest_expiry = spy.options[0]
            chain = spy.option_chain(nearest_expiry)

            puts_volume = chain.puts["volume"].sum()
            calls_volume = chain.calls["volume"].sum()

            if calls_volume > 0:
                ratio = puts_volume / calls_volume
                logger.info(f"Put/Call: using SPY options fallback — {ratio:.3f}")
                return ratio
            return None
        except Exception:
            logger.debug("Put/Call: fallback also failed", exc_info=True)
            return None
