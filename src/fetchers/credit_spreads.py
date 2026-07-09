"""High Yield Credit Spreads fetcher via FRED."""

from __future__ import annotations

import logging

import httpx

from ..models import Signal, SignalSource
from .base import BaseFetcher
from .fred import fetch_fred_series

logger = logging.getLogger(__name__)


class CreditSpreadsFetcher(BaseFetcher):
    """Fetches ICE BofA US High Yield Index Option-Adjusted Spread."""

    name = "High Yield Credit Spreads"
    series_id = "BAMLH0A0HYM2"

    async def fetch(self) -> Signal:
        """Fetch the latest HY spread from FRED."""
        logger.info(f"Fetching {self.name} ({self.series_id}) from FRED...")

        async with httpx.AsyncClient(timeout=15.0) as client:
            spread = await fetch_fred_series(client, self.series_id)

        if spread is None:
            raise ValueError(f"No data returned for {self.series_id}")

        # Spread is presented in percent (e.g. 5.12).

        # Determine summary trend
        signal_state = "Neutral"
        if spread > 5.0:
            signal_state = "Elevated (Risk-off / Stress)"
        elif spread < 3.5:
            signal_state = "Tight (Risk-on / Complacency)"
        else:
            signal_state = "Moderate (Normal)"

        summary = f"Credit Spreads: {spread:.2f}% — {signal_state}"

        return Signal(
            source=SignalSource.CREDIT_SPREADS,
            value=spread,
            metadata={
                "series_id": self.series_id,
                "state": signal_state,
            },
            summary=summary,
        )
