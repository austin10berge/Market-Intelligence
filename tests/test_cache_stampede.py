"""Tests for the per-cache-key stampede-protection lock in src.cache."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from src.cache import get_cache_lock


@pytest.mark.asyncio
async def test_get_cache_lock_returns_same_lock_for_same_key():
    lock_a = get_cache_lock("screener:csp")
    lock_b = get_cache_lock("screener:csp")
    assert lock_a is lock_b


@pytest.mark.asyncio
async def test_get_cache_lock_returns_different_locks_for_different_keys():
    lock_a = get_cache_lock("screener:csp")
    lock_b = get_cache_lock("screener:leaps")
    assert lock_a is not lock_b


@pytest.mark.asyncio
async def test_get_cache_lock_actually_serializes_concurrent_holders():
    lock = get_cache_lock("screener:stampede-test")
    order: list[str] = []

    async def holder(name: str, hold_seconds: float) -> None:
        async with lock:
            order.append(f"{name}-start")
            await asyncio.sleep(hold_seconds)
            order.append(f"{name}-end")

    await asyncio.gather(holder("first", 0.05), holder("second", 0.0))

    # The second coroutine must not start until the first has fully released the lock.
    assert order == ["first-start", "first-end", "second-start", "second-end"]


def _fake_cache_pair():
    """An in-memory cache_get/cache_set double backed by a real dict.

    The stampede-protection design relies on a request that waited behind the lock
    re-checking the cache and finding what the *other* request just wrote there. A
    cache_get double that always returns None (as a bare `AsyncMock(return_value=None)`
    would) can never exercise that path — the recheck would never find anything, and
    a passing `call_count == 1` assertion would be unreachable no matter how the route
    is implemented. This fake actually stores what cache_set writes so the recheck has
    something real to find, which is what lets these tests prove the dedup behavior.
    """
    store: dict[str, dict] = {}

    async def fake_get(key):
        return store.get(key)

    async def fake_set(key, data, ttl=None):
        store[key] = {
            "data": data,
            "cached_at": "2026-07-30T00:00:00+00:00",
            "market_status": "closed",
        }

    return fake_get, fake_set


@pytest.mark.asyncio
async def test_csp_endpoint_dedupes_concurrent_cache_misses(monkeypatch):
    from src.api import main as api_main

    fake_cache_get, fake_cache_set = _fake_cache_pair()
    monkeypatch.setattr(api_main, "cache_get", fake_cache_get)
    monkeypatch.setattr(api_main, "cache_set", fake_cache_set)

    call_count = 0

    async def slow_screen(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [{"symbol": "AAPL"}]

    with patch.object(api_main, "screen_csp_candidates", side_effect=lambda *a, **k: [{"symbol": "AAPL"}]):
        with patch("asyncio.to_thread", side_effect=slow_screen):
            results = await asyncio.gather(
                api_main.get_csp_candidates(), api_main.get_csp_candidates()
            )

    assert call_count == 1
    assert results[0]["candidates"] == [{"symbol": "AAPL"}]
    assert results[1]["candidates"] == [{"symbol": "AAPL"}]
