# Screener Cache-Miss Stampede Protection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent two requests that land right after a cache TTL expiry (or right after a watchlist edit invalidates the cache) from both triggering a full live scan concurrently on any of the 4 screener GET routes in `src/api/main.py` — worst case today is `/api/screener/csp-scan` (a multi-minute, whole-universe scan) running twice at once.

**Architecture:** Add an in-process, per-cache-key `asyncio.Lock` registry to `src/cache.py`, modeled on the existing per-event-loop lock pattern in `src/fetchers/market_overview.py` (`_get_yf_download_lock`). Each of the 4 screener routes wraps its "cache miss → compute → store" path in the key's lock, and re-checks the cache *after* acquiring the lock — so a request that waited behind another request's in-flight scan gets served the result that scan just cached, instead of running its own redundant scan.

**Tech Stack:** Python, FastAPI, asyncio, Redis (via `src/cache.py`), pytest, pytest-asyncio.

## Global Constraints

- The API runs as a single uvicorn process with no `--workers` flag (confirmed in `Dockerfile:32`), so an in-process lock is sufficient — no cross-process coordination (e.g. Redis-based distributed lock) is needed or in scope.
- Do not change response shape, TTL values, or the "zero-universe results are never cached" rule for `/api/screener/csp-scan` (`src/api/main.py:472-478`).
- Do not add a lock-eviction/cleanup mechanism — `/api/screener/csp-scan` has a per-param-combination cache key (`cache_key_suffix()`), so the lock registry can grow one entry per distinct filter combination a user has ever tried. This is an intentional, accepted tradeoff (single-user dashboard, bounded number of realistic filter combos) — do not build LRU eviction for it, that's out of scope.

---

## File Structure

- Modify: `src/cache.py` — add `get_cache_lock(key: str) -> asyncio.Lock`, a per-event-loop-per-key lock registry.
- Modify: `src/api/main.py` — wrap the 4 screener GET routes' cache-miss paths in `get_cache_lock(...)`, with a re-check of the cache immediately after acquiring the lock.
- Test: new file `tests/test_cache_stampede.py` — concurrency tests proving two simultaneous requests to the same cache key only compute once.

## Interfaces

- `get_cache_lock(key: str) -> asyncio.Lock` (cache.py) — returns the same `asyncio.Lock` instance for the same `key` within a given running event loop; a fresh lock the first time a key is seen.

---

### Task 1: Add the per-key lock registry to `cache.py`

**Files:**
- Modify: `src/cache.py`
- Test: `tests/test_cache_stampede.py` (new file)

**Interfaces:**
- Produces: `get_cache_lock(key: str) -> asyncio.Lock`

- [ ] **Step 1: Write the failing test**

Create `tests/test_cache_stampede.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache_stampede.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_cache_lock' from 'src.cache'`.

- [ ] **Step 3: Implement `get_cache_lock` in `src/cache.py`**

Add near the top of `src/cache.py`, after the existing imports (check what's already imported — `asyncio` is likely not yet imported in this file, add it; `weakref` will need adding too):

```python
import asyncio
import weakref

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache_stampede.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cache.py tests/test_cache_stampede.py
git commit -m "feat(cache): add per-key asyncio.Lock registry for cache-miss stampede protection"
```

---

### Task 2: Apply the lock to `/api/screener/csp`

**Files:**
- Modify: `src/api/main.py:357-372` (`get_csp_candidates`)
- Test: `tests/test_cache_stampede.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache_stampede.py` — use FastAPI's `TestClient` or direct async calls against the route function, following whatever pattern this repo's existing API tests use (check for a `tests/test_api_*.py` or similar for the `httpx.AsyncClient` + `ASGITransport` convention already in use; mock `screen_csp_candidates` to count calls and take a small `await asyncio.sleep(...)` so two concurrent requests actually overlap):

```python
import asyncio
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.asyncio
async def test_csp_endpoint_dedupes_concurrent_cache_misses(monkeypatch):
    from src import cache as cache_module
    from src.api import main as api_main

    monkeypatch.setattr(api_main, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(api_main, "cache_set", AsyncMock(return_value=None))

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
```

Note for the implementer: check `tests/` for an existing convention of testing `api/main.py` route functions directly (as plain async function calls, since FastAPI route functions are just coroutines) versus needing a full `TestClient`. If route functions have unresolved dependencies (e.g. `Response` param defaults), prefer calling the simpler routes (`get_csp_candidates`, `get_leaps_candidates`) directly as shown above; adapt for the two routes with query parameters in Tasks 3 and 4.

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache_stampede.py::test_csp_endpoint_dedupes_concurrent_cache_misses -v`
Expected: FAIL — `call_count == 2` (both concurrent requests ran the full scan).

- [ ] **Step 3: Implement the lock + re-check in `get_csp_candidates`**

In `src/api/main.py`, replace the body of `get_csp_candidates` (around line 357-372):

```python
@app.get("/api/screener/csp")
async def get_csp_candidates():
    """Return top Cash Secured Put candidates. Market-hours-aware TTL caching."""
    envelope = await cache_get(KEY_SCREENER_CSP)
    if envelope is not None:
        return {"candidates": envelope["data"], **_cache_meta(envelope)}

    async with get_cache_lock(KEY_SCREENER_CSP):
        # Re-check: another request may have populated the cache while we waited.
        envelope = await cache_get(KEY_SCREENER_CSP)
        if envelope is not None:
            return {"candidates": envelope["data"], **_cache_meta(envelope)}

        try:
            candidates = await asyncio.to_thread(screen_csp_candidates)
            ttl = screener_ttl()
            await cache_set(KEY_SCREENER_CSP, candidates, ttl=ttl)
            now_iso = datetime.now(timezone.utc).isoformat()
            return {"candidates": candidates, **_cache_meta(None, cached_at=now_iso)}
        except Exception as e:
            logger.exception("CSP screener failed")
            raise HTTPException(status_code=500, detail=str(e))
```

Add `get_cache_lock` to the existing import from `.cache` at the top of `src/api/main.py` (find the block importing `invalidate_screener_cache`, `screener_ttl`, etc. and add `get_cache_lock` to it).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache_stampede.py::test_csp_endpoint_dedupes_concurrent_cache_misses -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/main.py tests/test_cache_stampede.py
git commit -m "fix(api): serialize concurrent cache misses on /api/screener/csp"
```

---

### Task 3: Apply the lock to `/api/screener/csp-scan`

**Files:**
- Modify: `src/api/main.py:389-483` (`get_csp_scan_candidates`)
- Test: `tests/test_cache_stampede.py`

**Interfaces:**
- Consumes: `get_cache_lock` from Task 1.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cache_stampede.py`, mirroring Task 2's test but calling `get_csp_scan_candidates()` with default params (all `None`) and mocking `run_csp_scan` instead of `screen_csp_candidates`. Assert `run_csp_scan` (patched via `asyncio.to_thread`, same pattern as Task 2) is invoked exactly once across two concurrent calls, and that the mocked result includes `filter_summary.combined_unique > 0` so the cache-write branch is exercised (not the zero-universe skip-cache branch):

```python
@pytest.mark.asyncio
async def test_csp_scan_endpoint_dedupes_concurrent_cache_misses(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(api_main, "cache_set", AsyncMock(return_value=None))

    call_count = 0
    fake_result = {"candidates": [], "filter_summary": {"combined_unique": 5}}

    async def slow_scan(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return dict(fake_result)

    with patch("asyncio.to_thread", side_effect=slow_scan):
        results = await asyncio.gather(
            api_main.get_csp_scan_candidates(), api_main.get_csp_scan_candidates()
        )

    assert call_count == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cache_stampede.py::test_csp_scan_endpoint_dedupes_concurrent_cache_misses -v`
Expected: FAIL — `call_count == 2`.

- [ ] **Step 3: Implement the lock + re-check in `get_csp_scan_candidates`**

In `src/api/main.py`, the current body (after building `params` and `cache_key`) is:

```python
    envelope = await cache_get(cache_key)
    if envelope is not None:
        payload = envelope["data"]
        payload.update(_cache_meta(envelope))
        return payload

    try:
        result = await asyncio.to_thread(run_csp_scan, params)
        ...
```

Replace with:

```python
    envelope = await cache_get(cache_key)
    if envelope is not None:
        payload = envelope["data"]
        payload.update(_cache_meta(envelope))
        return payload

    async with get_cache_lock(cache_key):
        envelope = await cache_get(cache_key)
        if envelope is not None:
            payload = envelope["data"]
            payload.update(_cache_meta(envelope))
            return payload

        try:
            result = await asyncio.to_thread(run_csp_scan, params)

            summary = result.get("filter_summary", {})
            if summary.get("combined_unique", 0) > 0:
                await cache_set(cache_key, result, ttl=scanner_ttl())
            else:
                logger.warning(
                    "CSP scan returned zero universe tickers — result not cached. "
                    "Params: %s", params
                )

            now_iso = datetime.now(timezone.utc).isoformat()
            result.update(_cache_meta(None, cached_at=now_iso))
            return result
        except Exception as e:
            logger.exception("CSP scan failed")
            raise HTTPException(status_code=500, detail=str(e))
```

(This is the same body as before, just re-indented one level inside the `async with` block, with the cache re-check added right after entering it.)

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cache_stampede.py::test_csp_scan_endpoint_dedupes_concurrent_cache_misses -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/api/main.py tests/test_cache_stampede.py
git commit -m "fix(api): serialize concurrent cache misses on /api/screener/csp-scan"
```

---

### Task 4: Apply the lock to `/api/screener/leaps` and both branches of `/api/screener/stocks`

**Files:**
- Modify: `src/api/main.py:553-611` (`get_leaps_candidates`, `get_stock_candidates`)
- Test: `tests/test_cache_stampede.py`

**Interfaces:**
- Consumes: `get_cache_lock` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add two tests to `tests/test_cache_stampede.py`, following the exact pattern from Task 2 (`get_csp_candidates`):

```python
@pytest.mark.asyncio
async def test_leaps_endpoint_dedupes_concurrent_cache_misses(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(api_main, "cache_set", AsyncMock(return_value=None))
    call_count = 0

    async def slow(*a, **k):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [{"symbol": "MSFT"}]

    with patch("asyncio.to_thread", side_effect=slow):
        await asyncio.gather(api_main.get_leaps_candidates(), api_main.get_leaps_candidates())

    assert call_count == 1


@pytest.mark.asyncio
async def test_stocks_endpoint_default_watchlist_dedupes_concurrent_cache_misses(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(api_main, "cache_set", AsyncMock(return_value=None))
    call_count = 0

    async def slow(*a, **k):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [{"symbol": "SOFI"}]

    with patch("asyncio.to_thread", side_effect=slow):
        await asyncio.gather(
            api_main.get_stock_candidates(tickers=None),
            api_main.get_stock_candidates(tickers=None),
        )

    assert call_count == 1


@pytest.mark.asyncio
async def test_stocks_endpoint_dynamic_tickers_dedupes_concurrent_cache_misses(monkeypatch):
    from src.api import main as api_main

    monkeypatch.setattr(api_main, "cache_get", AsyncMock(return_value=None))
    monkeypatch.setattr(api_main, "cache_set", AsyncMock(return_value=None))
    call_count = 0

    async def slow(*a, **k):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return [{"symbol": "SOFI"}]

    with patch("asyncio.to_thread", side_effect=slow):
        await asyncio.gather(
            api_main.get_stock_candidates(tickers="SOFI"),
            api_main.get_stock_candidates(tickers="SOFI"),
        )

    assert call_count == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cache_stampede.py -k "leaps_endpoint or stocks_endpoint" -v`
Expected: All 3 FAIL — each with `call_count == 2`.

- [ ] **Step 3: Implement the lock + re-check in both routes**

In `src/api/main.py`, apply the identical lock-wrap-and-recheck transformation from Task 2/3 to:

1. `get_leaps_candidates` (around line 553-568) — wrap the cache-miss body in `async with get_cache_lock(KEY_SCREENER_LEAPS):` with a re-check.
2. `get_stock_candidates` (around line 573-611) — this route has **two** cache-miss bodies (the `if tickers:` branch using `cache_key = f"{KEY_SCREENER_STOCKS}:{...}"`, and the fallback branch using `KEY_SCREENER_STOCKS`). Wrap **each** independently in `async with get_cache_lock(<that branch's cache_key>):` with its own re-check — they use different keys so they need separate lock acquisitions, not one shared lock.

Follow the exact same code shape as Task 2's `get_csp_candidates` change for each.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cache_stampede.py -v`
Expected: All tests in the file PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS — confirm no other test patches `asyncio.to_thread` globally in a way that conflicts with the new `async with` blocks, and no test asserts on the exact line-by-line structure of these routes.

- [ ] **Step 6: Commit**

```bash
git add src/api/main.py tests/test_cache_stampede.py
git commit -m "fix(api): serialize concurrent cache misses on /api/screener/leaps and /api/screener/stocks"
```
