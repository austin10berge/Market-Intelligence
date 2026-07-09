"""Gamma Exposure (GEX) and DIX fetcher via SqueezeMetrics."""

from __future__ import annotations

import csv
import logging
from io import StringIO

import httpx

from ..models import Signal, SignalSource
from .base import BaseFetcher

logger = logging.getLogger(__name__)


class GexFetcher(BaseFetcher):
    """Fetches Gamma Exposure from SqueezeMetrics daily CSV."""

    name = "Gamma Exposure (GEX)"
    url = "https://squeezemetrics.com/monitor/static/DIX.csv"

    async def fetch(self) -> Signal:
        """Fetch and parse DIX.csv."""
        logger.info(f"Fetching GEX data from {self.url}...")

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(self.url)
            resp.raise_for_status()

        # Parse CSV (format: date,price,dix,gex)
        csv_data = resp.text
        f = StringIO(csv_data)
        reader = csv.reader(f)

        # Skip header
        next(reader, None)

        # Get the latest row
        rows = list(reader)
        if not rows:
            raise ValueError("SqueezeMetrics CSV is empty")

        latest_row = rows[-1]
        date_str, price, dix_str, gex_str = latest_row

        gex_raw = float(gex_str)
        dix_raw = float(dix_str)

        # Convert GEX to billions for readability
        gex_billions = gex_raw / 1_000_000_000.0

        # Determine summary trend
        signal_state = "Neutral"
        if gex_billions < 0:
            signal_state = "Negative (High volatility, unstable)"
        elif gex_billions > 5:
            signal_state = "High Positive (Price pinning, calm)"
        else:
            signal_state = "Positive (Normal)"

        summary = f"GEX: ${gex_billions:+.2f}B | DIX: {dix_raw * 100:.1f}% — {signal_state}"

        return Signal(
            source=SignalSource.GEX,
            value=gex_billions,
            metadata={
                "date": date_str,
                "price": float(price),
                "dix": dix_raw,
                "gex_raw": gex_raw,
                "state": signal_state,
            },
            summary=summary,
        )
