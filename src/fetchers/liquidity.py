"""Net Liquidity fetcher (TGA proxy) via FRED."""

from __future__ import annotations

import asyncio
import logging

import httpx

from ..models import Signal, SignalSource
from .base import BaseFetcher
from .fred import fetch_fred_series

logger = logging.getLogger(__name__)


class LiquidityFetcher(BaseFetcher):
    """Fetches Net Liquidity proxy: Fed Assets (WALCL) - TGA (WTREGEN) - RevRepo (RRPONTSYD)."""

    name = "Net Liquidity Proxy (TGA)"

    async def fetch(self) -> Signal:
        """Fetch balance sheet components and compute net liquidity."""
        logger.info(f"Fetching {self.name} from FRED...")

        async with httpx.AsyncClient(timeout=15.0) as client:
            # Fetch all 3 series concurrently
            assets_task = fetch_fred_series(client, "WALCL")
            tga_task = fetch_fred_series(client, "WTREGEN")
            rrp_task = fetch_fred_series(client, "RRPONTSYD")

            assets, tga, rrp = await asyncio.gather(assets_task, tga_task, rrp_task)

        if any(x is None for x in (assets, tga, rrp)):
            raise ValueError("Failed to fetch one or more liquidity components from FRED")

        # FRED values for these series are in Millions.
        # Compute net liquidity
        net_liquidity = assets - tga - rrp

        # Convert to Trillions for display
        net_liq_trillions = net_liquidity / 1_000_000.0
        tga_trillions = tga / 1_000_000.0
        rrp_trillions = rrp / 1_000_000.0
        assets_trillions = assets / 1_000_000.0

        summary = f"Net Liquidity: ${net_liq_trillions:.2f}T (TGA: ${tga_trillions:.2f}T | RRP: ${rrp_trillions:.2f}T)"

        return Signal(
            source=SignalSource.LIQUIDITY,
            value=net_liquidity,  # raw value in millions
            metadata={
                "assets": assets,
                "tga": tga,
                "rrp": rrp,
                "display_trillions": net_liq_trillions,
            },
            summary=summary,
        )
