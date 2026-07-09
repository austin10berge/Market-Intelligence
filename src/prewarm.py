"""
Cache pre-warmer — runs at 9:25 AM ET (Mon–Fri) via cron before market open.

Populates the screener Redis keys so the first dashboard request after 9:30 AM open
is served from cache rather than triggering a cold yfinance fetch.

Cron entry (LXC host, in ET timezone):
    25 9 * * 1-5  docker compose -f /root/market-intelligence/docker-compose.yml run --rm prewarm

Does NOT re-warm if the key is already warm (avoids duplicate work if run twice).
"""

from __future__ import annotations

import asyncio
import logging

from .cache import (
    KEY_SCREENER_CSP,
    KEY_SCREENER_LEAPS,
    KEY_SCREENER_STOCKS,
    cache_set,
    is_cache_warm,
    screener_ttl,
)
from .screener.options import screen_csp_candidates, screen_leaps_candidates
from .screener.stocks import screen_stocks

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def prewarm() -> None:
    tasks = [
        _warm_key("CSP screener",   KEY_SCREENER_CSP,   screen_csp_candidates),
        _warm_key("LEAPS screener", KEY_SCREENER_LEAPS, screen_leaps_candidates),
        _warm_key("Stock screener", KEY_SCREENER_STOCKS, screen_stocks),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    errors = [r for r in results if isinstance(r, Exception)]
    if errors:
        for err in errors:
            logger.error("Pre-warm error: %s", err)
    else:
        logger.info("Pre-warm complete — all screener caches are warm.")


async def _warm_key(label: str, key: str, fetch_fn) -> None:
    """Populate a single screener cache key unless it is already warm."""
    if await is_cache_warm(key):
        logger.info("%s already cached — skipping.", label)
        return

    logger.info("Pre-warming %s...", label)
    # Screener functions are synchronous (yfinance/Alpaca) — run in thread pool
    data = await asyncio.get_event_loop().run_in_executor(None, fetch_fn)
    ttl = screener_ttl()
    await cache_set(key, data, ttl=ttl)
    logger.info("%s pre-warmed (%d items, TTL %ds).", label, len(data) if data else 0, ttl)


if __name__ == "__main__":
    asyncio.run(prewarm())
