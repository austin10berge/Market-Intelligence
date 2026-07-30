"""VIX spot price and term structure fetcher."""

from __future__ import annotations

import asyncio
import logging

import yfinance as yf

from ..models import Signal, SignalSource
from .base import BaseFetcher

logger = logging.getLogger(__name__)

# VIX tickers for term structure analysis
VIX_SPOT = "^VIX"
VIX_3M = "^VIX3M"  # 3-month VIX — compare with spot for term structure


class VixFetcher(BaseFetcher):
    """Fetch VIX spot and term structure (contango vs. backwardation)."""

    @property
    def name(self) -> str:
        return "VIX Term Structure"

    async def fetch(self) -> Signal | None:
        def _fetch_fast_info() -> tuple[float | None, float | None]:
            spot_ticker = yf.Ticker(VIX_SPOT)
            vix3m_ticker = yf.Ticker(VIX_3M)
            spot = getattr(spot_ticker.fast_info, "last_price", None)
            vix3m = getattr(vix3m_ticker.fast_info, "last_price", None)
            return spot, vix3m

        spot_price, vix3m_price = await asyncio.to_thread(_fetch_fast_info)

        if spot_price is None:
            logger.warning("VIX: unable to retrieve spot price")
            return None

        spot_price = round(float(spot_price), 2)
        metadata: dict = {"spot": spot_price}

        # Term structure analysis
        if vix3m_price is not None:
            vix3m_price = round(float(vix3m_price), 2)
            spread = round(vix3m_price - spot_price, 2)
            ratio = round(vix3m_price / spot_price, 3) if spot_price > 0 else None

            # Contango = VIX3M > VIX (normal/calm), Backwardation = VIX > VIX3M (stress)
            if spread > 0.5:
                structure = "Contango"
                stress_note = "— normal, calm"
            elif spread < -0.5:
                structure = "Backwardation"
                stress_note = "— stress building"
            else:
                structure = "Flat"
                stress_note = "— transition zone"

            metadata.update(
                {
                    "vix3m": vix3m_price,
                    "spread": spread,
                    "ratio": ratio,
                    "term_structure": structure,
                }
            )

            summary = (
                f"VIX: {spot_price} | Term structure: {structure} "
                f"(spread {spread:+.2f}) {stress_note}"
            )
        else:
            structure = "Unknown"
            summary = f"VIX: {spot_price} (term structure unavailable)"
            metadata["term_structure"] = structure

        return Signal(
            source=SignalSource.VIX,
            value=spot_price,
            metadata=metadata,
            summary=summary,
        )
