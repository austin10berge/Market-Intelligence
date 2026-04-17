"""Abstract base class for all data fetchers."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from ..models import Signal

logger = logging.getLogger(__name__)

# Shared async HTTP client — reused across all fetchers in a single pipeline run
_client: httpx.AsyncClient | None = None


async def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared async HTTP client."""
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=10.0),
            follow_redirects=True,
            headers={"User-Agent": "MarketIntelligence/0.1"},
        )
    return _client


async def close_http_client() -> None:
    """Close the shared HTTP client."""
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
        _client = None


class BaseFetcher(ABC):
    """Base class for all market data fetchers.

    Subclasses must implement:
      - name: human-readable name
      - fetch(): async method that returns a Signal or None on failure
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this data source."""
        ...

    @abstractmethod
    async def fetch(self) -> Signal | None:
        """Fetch data from the source and return a Signal, or None on failure."""
        ...

    async def safe_fetch(self) -> Signal | None:
        """Fetch with error handling — logs failures but doesn't crash the pipeline."""
        try:
            signal = await self.fetch()
            if signal:
                logger.info(f"✅ {self.name}: {signal.summary or signal.value}")
            return signal
        except Exception:
            logger.exception(f"❌ {self.name}: fetch failed")
            return None
