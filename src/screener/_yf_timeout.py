"""Hard timeout wrapper for blocking yfinance calls.

yfinance's HTTP calls have no built-in timeout. When Yahoo Finance stalls or
throttles, a call blocks forever. Screener functions run inside
``asyncio.to_thread``, which draws from the same shared executor FastAPI uses
for every synchronous request — one indefinitely-hung yfinance call
permanently occupies a worker thread, and enough of them exhaust the pool and
start blocking unrelated, otherwise-fast endpoints too.

``call_with_timeout`` runs the call in its own single-use thread so a timeout
only ever leaks that one dedicated thread, never a thread borrowed from the
shared pool.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

logger = logging.getLogger(__name__)

YF_CALL_TIMEOUT = 15  # seconds


def call_with_timeout[T](
    fn: Callable[[], T], *, timeout: float = YF_CALL_TIMEOUT, label: str = ""
) -> T | None:
    """Run `fn` (a zero-arg callable) and return its result, or None if it
    hasn't finished within `timeout` seconds.

    On timeout, the underlying call keeps running in its own thread — that
    thread is abandoned (not the shared executor pool) and will be garbage
    collected once it eventually finishes.
    """
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        future = executor.submit(fn)
        try:
            return future.result(timeout=timeout)
        except FutureTimeoutError:
            suffix = f" ({label})" if label else ""
            logger.warning("yfinance call timed out after %ss%s", timeout, suffix)
            return None
    finally:
        executor.shutdown(wait=False)
