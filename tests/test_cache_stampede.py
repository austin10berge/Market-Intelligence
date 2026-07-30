"""Tests for the per-cache-key stampede-protection lock in src.cache."""

from __future__ import annotations

import asyncio

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
