"""
Market-hours-aware Redis cache layer.

TTL Strategy
------------
Screener endpoints (CSP, LEAPS, Stocks):
  • During market hours (Mon–Fri 9:30–16:00 ET): 5-minute TTL
    — Prices move, so we refresh frequently but don't hammer yfinance on every request.
  • Outside market hours: cache until the next market open (up to ~17h on weeknights,
    ~65h over a weekend). The market is closed, so the data cannot change.

Market-posture endpoint:
  • Only refreshed by the nightly pipeline. No TTL expiry — invalidated explicitly
    when a new digest is written to the DB (via invalidate_market_posture()).

All cache entries store a `cached_at` ISO timestamp and a `market_status` string
so the frontend can surface "Last updated X minutes ago (market closed)" to the user.
"""

from __future__ import annotations

import asyncio
import json
import logging
import weakref
import zoneinfo
from datetime import date, datetime, timedelta
from typing import Any

import holidays
import redis.asyncio as aioredis

from .config import settings

logger = logging.getLogger(__name__)

ET = zoneinfo.ZoneInfo("America/New_York")

# ── Cache-miss stampede protection ─────────────────────────────────────────────
#
# Two requests landing right after a TTL expiry (or a watchlist-edit cache
# invalidation) would otherwise both fall through to a live compute — worst
# case, /api/screener/csp-scan (a multi-minute whole-universe scan) running
# twice concurrently. This registry hands out one asyncio.Lock per cache key
# so the second request waits for the first's in-flight compute and then
# reads the result it just cached, instead of recomputing.
#
# Keyed per event loop (not a single module-level dict) for the same reason
# market_overview.py's yf.download lock is: asyncio.Lock binds to whichever
# loop first acquires it, and pytest-asyncio gives each test its own loop.
# Production only ever runs one loop, so this reduces to one registry in practice.
_cache_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)


def get_cache_lock(key: str) -> asyncio.Lock:
    """Return the asyncio.Lock guarding computation of the given cache key."""
    loop = asyncio.get_running_loop()
    loop_locks = _cache_locks.get(loop)
    if loop_locks is None:
        loop_locks = {}
        _cache_locks[loop] = loop_locks
    lock = loop_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        loop_locks[key] = lock
    return lock


# ── Market-hours helpers ──────────────────────────────────────────────────────

MARKET_OPEN = (9, 30)  # 9:30 AM ET
MARKET_CLOSE = (16, 0)  # 4:00 PM ET

_NYSE_HOLIDAYS = holidays.financial_holidays("NYSE")


def is_trading_day(d: date) -> bool:
    """Return True if `d` is a US equities trading day (Mon-Fri, non-NYSE-holiday)."""
    return d.weekday() < 5 and d not in _NYSE_HOLIDAYS


def market_is_open() -> bool:
    """Return True if US equities market is currently open (trading day, 9:30–16:00 ET)."""
    now = datetime.now(ET)
    if not is_trading_day(now.date()):
        return False
    t = (now.hour, now.minute)
    return MARKET_OPEN <= t < MARKET_CLOSE


def market_status_label() -> str:
    """Human-readable market status string for the UI."""
    return "Market Open" if market_is_open() else "Market Closed"


def seconds_until_next_open() -> int:
    """Return seconds from now until the next weekday 9:30 AM ET open.

    Handles weekday evenings and the Friday-through-Sunday gap correctly.
    """
    now = datetime.now(ET)

    # Build a candidate open time: today at 9:30 AM ET
    candidate = now.replace(hour=MARKET_OPEN[0], minute=MARKET_OPEN[1], second=0, microsecond=0)

    # If today's open has already passed (or is right now), start from tomorrow
    if candidate <= now:
        candidate += timedelta(days=1)

    # Skip weekends and NYSE holidays
    while not is_trading_day(candidate.date()):
        candidate += timedelta(days=1)

    delta = (candidate - now).total_seconds()
    return max(int(delta), 1)  # never return 0 or negative


def screener_ttl() -> int:
    """Dynamic TTL (seconds) for screener cache entries.

    - Market open : 5 minutes (prices move, refresh often)
    - Market closed weekday : until next open (data won't change)
    - Weekend : 4 hours max so a bad result doesn't stay locked in
      until Monday morning (~57 h away on Sunday night)
    """
    if market_is_open():
        return 300  # 5 minutes
    secs = seconds_until_next_open()
    four_hours = 4 * 3600
    return min(secs, four_hours)


def market_overview_ttl(has_partial_failure: bool) -> int:
    """TTL (seconds) for the market-overview cache entry.

    If any independent sub-fetch (sectors, VIX, GEX, breadth, themes) failed,
    cache briefly (60s) so a transient failure self-heals on the next request
    instead of being locked in for up to 4 hours via screener_ttl() when the
    market is closed. On a full success, use the same market-hours-aware TTL
    as other screener endpoints.
    """
    if has_partial_failure:
        return 60
    return screener_ttl()


def scanner_ttl() -> int:
    """TTL (seconds) for the broad universe CSP scanner cache entry.

    The scanner is designed to run once at or after EOD close and have its
    results persist until the following EOD. Unlike the watchlist screeners,
    which need to refresh during the trading day, the scanner captures a
    daily snapshot — running it intraday adds noise without adding value.

    TTL is fixed at 23 hours regardless of market status. This means:
    - A scan run at 4 PM ET is valid until 3 PM ET the next day.
    - The result survives overnight and through the pre-market window.
    - It does NOT auto-refresh during the trading day (intentional).
    - Use DELETE /api/screener/csp-scan to manually bust the cache
      and force a fresh scan at any time.
    """
    return 23 * 3600  # 23 hours


# ── Redis connection ──────────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return a module-level Redis client (lazy init, connection pool)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
    return _redis_client


# ── Cache key constants ───────────────────────────────────────────────────────

KEY_SCREENER_CSP = "screener:csp"
KEY_SCREENER_LEAPS = "screener:leaps"
KEY_SCREENER_STOCKS = "screener:stocks"
KEY_SCREENER_CSP_SCAN = "screener:csp-scan"
KEY_MARKET_POSTURE = "market:posture"
KEY_MARKET_OVERVIEW = "market:overview"


# ── Core get/set helpers ──────────────────────────────────────────────────────


async def cache_get(key: str) -> dict | None:
    """Retrieve a cached envelope by key. Returns the full envelope dict or None on miss/error."""
    try:
        raw = await get_redis().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        # Redis unavailable or decode error — treat as cache miss, never crash the API
        logger.warning("Redis GET failed for key '%s': %s", key, exc)
        return None


async def cache_set(key: str, data: Any, ttl: int) -> None:
    """Store data under key with the given TTL (seconds).

    The envelope written to Redis includes:
      - data         : the actual payload
      - cached_at    : ISO timestamp string (UTC)
      - market_status: "Market Open" | "Market Closed" at time of caching
      - ttl          : the TTL that was applied (informational, for debugging)
    """
    envelope = {
        "data": data,
        "cached_at": datetime.utcnow().isoformat() + "Z",
        "market_status": market_status_label(),
        "ttl": ttl,
    }
    try:
        # allow_nan=False ensures NaN floats raise immediately rather than storing
        # an invalid JSON token that FastAPI's JSONResponse (also allow_nan=False) would
        # fail to re-serialize on the way out, producing an opaque 500.
        await get_redis().set(key, json.dumps(envelope, allow_nan=False), ex=ttl)
    except Exception as exc:
        logger.warning("Redis SET failed for key '%s': %s", key, exc)


async def cache_delete(key: str) -> None:
    """Delete a single cache key (used for explicit invalidation)."""
    try:
        await get_redis().delete(key)
        logger.info("Cache invalidated: %s", key)
    except Exception as exc:
        logger.warning("Redis DELETE failed for key '%s': %s", key, exc)


async def cache_delete_pattern(pattern: str) -> None:
    """Delete all keys matching a glob pattern (e.g. 'screener:*')."""
    try:
        client = get_redis()
        keys = await client.keys(pattern)
        if keys:
            await client.delete(*keys)
            logger.info("Cache invalidated %d key(s) matching '%s'", len(keys), pattern)
    except Exception as exc:
        logger.warning("Redis pattern DELETE failed for '%s': %s", pattern, exc)


# ── Domain-specific invalidation helpers ─────────────────────────────────────


async def invalidate_screener_cache() -> None:
    """Wipe all screener cache entries. Call when watchlist or CSP settings change."""
    await cache_delete_pattern("screener:*")


async def invalidate_market_posture() -> None:
    """Wipe the market-posture cache. Call after the nightly pipeline writes a new digest."""
    await cache_delete(KEY_MARKET_POSTURE)


# ── Pre-warm helper ───────────────────────────────────────────────────────────


async def is_cache_warm(key: str) -> bool:
    """Return True if a key already exists in Redis (avoids duplicate pre-warm work)."""
    try:
        return bool(await get_redis().exists(key))
    except Exception:
        return False
