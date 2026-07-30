"""Shared per-event-loop lock + retry wrapper for yf.download() calls.

yfinance's multi-ticker download populates a module-level global
(yfinance.shared._DFS) internally and reads results back out of it: two
yf.download() calls running concurrently in this process can race on that
global and each read back a mix of the other's tickers. Serializing every
call through a lock trades away intra-process download parallelism for
correctness — confirmed live in market_overview.py that concurrent
chunk/sector/VIX calls were silently returning each other's data.

Every fetcher that calls yf.download() must go through download_with_retry()
rather than calling yf.download() directly, so this protection actually
covers the whole pipeline instead of just the fetcher that happened to hit
the bug first.

The lock is keyed per event loop (not a single module-level instance):
asyncio.Lock binds to whichever loop first acquires it, and a plain
module-level lock would raise "bound to a different event loop" the moment
a second loop touches it (e.g. every pytest-asyncio test gets its own loop).
Production only ever runs one loop, so this reduces to one lock in practice.
"""

from __future__ import annotations

import asyncio
import logging
import weakref

import yfinance as yf

logger = logging.getLogger(__name__)

_RETRIES = 2
_RETRY_BACKOFF_S = 1.5

_yf_download_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock] = (
    weakref.WeakKeyDictionary()
)


def _get_yf_download_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _yf_download_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _yf_download_locks[loop] = lock
    return lock


async def download_with_retry(*args, **kwargs):
    """Retry a yf.download call a couple of times before giving up.

    Yahoo Finance intermittently drops tickers or errors out entirely under
    rate limiting; a short retry with backoff self-heals most of these without
    adding meaningful latency to the request. Every call is serialized through
    the shared per-loop lock — see module docstring.
    """
    last_exc: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            async with _get_yf_download_lock():
                return await asyncio.to_thread(yf.download, *args, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt < _RETRIES:
                logger.warning(
                    "yf.download failed (attempt %d/%d), retrying: %s",
                    attempt + 1,
                    _RETRIES + 1,
                    exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    raise last_exc
