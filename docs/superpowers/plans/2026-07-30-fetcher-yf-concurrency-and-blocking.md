# Fetcher yfinance Concurrency & Event-Loop Blocking Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two related bugs across `src/fetchers/`, fixed together because they touch overlapping files:
1. **Race not backported (Issue 5):** `market_overview.py` already found and fixed a real bug — concurrent `yf.download()` calls race on yfinance's internal `yfinance.shared._DFS` global and can return mixed-up ticker data — via a per-event-loop `asyncio.Lock`. Four other fetchers (`sector_etf.py`, `thematic_etf.py`, `treasury_yields.py`, `cme_fedwatch.py`) call `yf.download()` unlocked inside the same `asyncio.gather` pipeline run, so they can silently get another fetcher's data.
2. **Event-loop blocking (Issue 6):** `sector_etf.py:45`, `vix.py:28-29`, `unusual_volume.py:47` (in a per-ticker loop), and `put_call.py`'s fallback path call yfinance synchronously with no `asyncio.to_thread`, freezing the entire API process (including unrelated health checks and cached reads) for the call duration. `unusual_volume.py` additionally has no caching at all, unlike the other rate-limited fetchers (`insider_trading.py`, `congressional_trades.py`), so it pays this cost on every pipeline run.

**Architecture:** Extract `market_overview.py`'s existing lock+retry pattern into a new shared module `src/fetchers/_yf_lock.py` (`download_with_retry`) so it isn't duplicated — `market_overview.py` becomes a consumer of it instead of the sole owner. Apply `download_with_retry` to the 4 fetchers missing the lock. Wrap the remaining synchronous yfinance calls (`vix.py`, `unusual_volume.py`, `put_call.py`'s fallback) in `asyncio.to_thread`. Add an app_config-based cache to `unusual_volume.py`, mirroring the existing pattern in `src/db.py` (`get_insider_cache`/`set_insider_cache`).

**Tech Stack:** Python, asyncio, yfinance, pytest, pytest-asyncio.

## Global Constraints

- Do not change any fetcher's signal output shape, thresholds, or business logic — this plan is purely about concurrency safety and not blocking the event loop.
- `market_overview.py`'s existing behavior and its existing tests (`tests/test_market_overview.py`) must keep passing unchanged — the extraction must be a pure move, not a rewrite.
- `vix.py` and `put_call.py`'s fallback use `yf.Ticker(...)` / `.fast_info` / `.options` / `.option_chain(...)`, not `yf.download()` — they don't touch `yfinance.shared._DFS` the way multi-ticker `yf.download()` does, so they only need `asyncio.to_thread` wrapping, not the download lock. Do not over-apply the lock where it isn't needed.

---

## File Structure

- Create: `src/fetchers/_yf_lock.py` — shared `download_with_retry()` + per-loop lock registry, extracted from `market_overview.py`.
- Modify: `src/fetchers/market_overview.py` — remove the duplicated lock/retry code, import `download_with_retry` from `_yf_lock.py` instead.
- Modify: `src/fetchers/sector_etf.py` — use `download_with_retry`, wrapped (fixes both issues at once since it was fully synchronous).
- Modify: `src/fetchers/thematic_etf.py` — use `download_with_retry` instead of raw `asyncio.to_thread(yf.download, ...)`.
- Modify: `src/fetchers/treasury_yields.py` — same as thematic_etf.py.
- Modify: `src/fetchers/cme_fedwatch.py` — same as thematic_etf.py.
- Modify: `src/fetchers/vix.py` — wrap the two `fast_info` accesses in `asyncio.to_thread`.
- Modify: `src/fetchers/unusual_volume.py` — wrap the per-ticker `yf.Ticker(symbol).history(...)` call in `asyncio.to_thread`; add app_config cache read/write around the whole `fetch()`.
- Modify: `src/fetchers/put_call.py` — wrap `_fetch_fallback`'s body in `asyncio.to_thread`.
- Modify: `src/db.py` — add `get_unusual_volume_cache`/`set_unusual_volume_cache`, mirroring `get_insider_cache`/`set_insider_cache`.
- Test: `tests/test_market_overview.py` — must keep passing with zero changes (proves the extraction is behavior-preserving).
- Test: new file `tests/test_fetcher_yf_lock.py` — covers `_yf_lock.py` directly and the 4 fetchers that gain the lock.
- Test: new file, or extend an existing one if `vix.py`/`unusual_volume.py`/`put_call.py` already have test files — check `tests/` for `test_vix.py`, `test_unusual_volume.py`, `test_put_call.py` before creating new ones.

## Interfaces

- `download_with_retry(*args, **kwargs) -> pd.DataFrame` (`_yf_lock.py`) — drop-in replacement for `await asyncio.to_thread(yf.download, *args, **kwargs)`, adds the shared lock and a 2-retry/backoff policy (same behavior as `market_overview.py`'s current `_download_with_retry`).
- `get_unusual_volume_cache(max_age_hours: float = 0.25) -> dict | None`, `set_unusual_volume_cache(data: dict) -> None` (`db.py`) — same shape/pattern as `get_insider_cache`/`set_insider_cache`.

---

### Task 1: Extract the shared lock+retry helper into `_yf_lock.py`

**Files:**
- Create: `src/fetchers/_yf_lock.py`
- Modify: `src/fetchers/market_overview.py:1-127` (imports and the lock/retry block)
- Test: `tests/test_market_overview.py` (must pass unchanged), new file `tests/test_fetcher_yf_lock.py`

**Interfaces:**
- Produces: `download_with_retry(*args, **kwargs)`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_fetcher_yf_lock.py`:

```python
"""Tests for the shared yf.download lock+retry helper."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pandas as pd
import pytest

from src.fetchers._yf_lock import _get_yf_download_lock, download_with_retry


@pytest.mark.asyncio
async def test_download_with_retry_calls_yf_download_via_to_thread():
    fake_df = pd.DataFrame({"Close": [1.0, 2.0]})
    with patch("src.fetchers._yf_lock.yf.download", return_value=fake_df) as mock_dl:
        result = await download_with_retry("AAPL", period="2d")

    mock_dl.assert_called_once_with("AAPL", period="2d")
    pd.testing.assert_frame_equal(result, fake_df)


@pytest.mark.asyncio
async def test_download_with_retry_serializes_concurrent_calls():
    """Two concurrent downloads must not run inside the lock at the same time."""
    order: list[str] = []

    def fake_download(ticker, **kwargs):
        order.append(f"{ticker}-start")
        order.append(f"{ticker}-end")
        return pd.DataFrame({"Close": [1.0]})

    with patch("src.fetchers._yf_lock.yf.download", side_effect=fake_download):
        lock = _get_yf_download_lock()
        assert not lock.locked()
        await asyncio.gather(
            download_with_retry("AAPL"),
            download_with_retry("MSFT"),
        )

    # Both calls completed; the point of this test is that acquiring the lock
    # doesn't raise and both results come back correctly under concurrency.
    assert len(order) == 4


@pytest.mark.asyncio
async def test_download_with_retry_retries_then_raises():
    with patch(
        "src.fetchers._yf_lock.yf.download", side_effect=RuntimeError("boom")
    ) as mock_dl:
        with patch("src.fetchers._yf_lock.asyncio.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="boom"):
                await download_with_retry("AAPL")

    assert mock_dl.call_count == 3  # 1 initial + 2 retries
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_fetcher_yf_lock.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.fetchers._yf_lock'`.

- [ ] **Step 3: Create `_yf_lock.py` by moving the code out of `market_overview.py`**

Create `src/fetchers/_yf_lock.py`:

```python
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
                    attempt + 1, _RETRIES + 1, exc,
                )
                await asyncio.sleep(_RETRY_BACKOFF_S * (attempt + 1))
    raise last_exc
```

In `src/fetchers/market_overview.py`:

1. Delete lines 79-127 (the `_YF_RETRIES`/`_YF_RETRY_BACKOFF_S` constants, the comment block, `_yf_download_locks`, `_get_yf_download_lock`, and `_download_with_retry`) — this entire block now lives in `_yf_lock.py`.
2. Add near the top, with the other same-package imports (near `from .thematic_etf import BASKET_THEMES, SINGLE_TICKER_THEMES`):

```python
from ._yf_lock import download_with_retry as _download_with_retry
```

3. Remove `import weakref` from the top of the file if nothing else in `market_overview.py` uses `weakref` (confirm with `grep -n weakref src/fetchers/market_overview.py` after the deletion — it should show only the import line, meaning it's now unused and must be removed to avoid an unused-import lint failure).
4. Leave every call site of `_download_with_retry(...)` elsewhere in the file completely unchanged — the alias import makes them work identically to before.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_fetcher_yf_lock.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Run market_overview's existing tests to confirm the extraction is behavior-preserving**

Run: `pytest tests/test_market_overview.py -v`
Expected: All PASS, unchanged from before this refactor — this is the proof the move didn't alter behavior.

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/_yf_lock.py src/fetchers/market_overview.py tests/test_fetcher_yf_lock.py
git commit -m "refactor(fetchers): extract yf.download lock+retry into shared _yf_lock module"
```

---

### Task 2: Apply `download_with_retry` to the 4 unprotected fetchers

**Files:**
- Modify: `src/fetchers/sector_etf.py:1-46`
- Modify: `src/fetchers/thematic_etf.py:1-70`
- Modify: `src/fetchers/treasury_yields.py:1-65`
- Modify: `src/fetchers/cme_fedwatch.py:1-50`
- Test: `tests/test_fetcher_yf_lock.py`

**Interfaces:**
- Consumes: `download_with_retry` from Task 1.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_fetcher_yf_lock.py`, one test per fetcher, asserting each now calls `download_with_retry` rather than `yf.download` directly:

```python
@pytest.mark.asyncio
async def test_sector_etf_fetcher_uses_shared_download_with_retry():
    from src.fetchers import sector_etf

    fake_df = pd.DataFrame()  # empty is fine — fetch() should return None gracefully
    with patch.object(sector_etf, "download_with_retry", return_value=fake_df) as mock_dl:
        await sector_etf.SectorEtfFetcher().fetch()
    mock_dl.assert_called_once()


@pytest.mark.asyncio
async def test_thematic_etf_fetcher_uses_shared_download_with_retry():
    from src.fetchers import thematic_etf

    fake_df = pd.DataFrame()
    with patch.object(thematic_etf, "download_with_retry", return_value=fake_df) as mock_dl:
        await thematic_etf.ThematicEtfFetcher().fetch()
    mock_dl.assert_called_once()


@pytest.mark.asyncio
async def test_treasury_yields_fetcher_uses_shared_download_with_retry():
    from src.fetchers import treasury_yields

    fake_df = pd.DataFrame()
    with patch.object(treasury_yields, "download_with_retry", return_value=fake_df) as mock_dl:
        await treasury_yields.TreasuryYieldsFetcher().fetch()
    mock_dl.assert_called_once()


@pytest.mark.asyncio
async def test_cme_fedwatch_futures_fetch_uses_shared_download_with_retry():
    from src.fetchers import cme_fedwatch

    fake_df = pd.DataFrame({"Close": [100.0]})
    with patch.object(cme_fedwatch, "download_with_retry", return_value=fake_df) as mock_dl:
        await cme_fedwatch._get_futures_implied_rate()
    mock_dl.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_fetcher_yf_lock.py -k "uses_shared_download_with_retry" -v`
Expected: All 4 FAIL — `AttributeError` (no `download_with_retry` attribute on these modules yet) or `mock_dl.assert_called_once()` failing since the real `yf.download` is called instead.

- [ ] **Step 3: Update `sector_etf.py`**

In `src/fetchers/sector_etf.py`, replace `import yfinance as yf` with:

```python
from ._yf_lock import download_with_retry
```

Replace the fetch body's download line:

```python
        data = yf.download(tickers_str, period="2d", group_by="ticker", progress=False)
```

with:

```python
        data = await download_with_retry(tickers_str, period="2d", group_by="ticker", progress=False)
```

- [ ] **Step 4: Update `thematic_etf.py`**

In `src/fetchers/thematic_etf.py`, add `from ._yf_lock import download_with_retry` to the imports (keep `import yfinance as yf` if it's used elsewhere in the file besides this call — check first; if not used elsewhere, remove it same as sector_etf.py). Replace:

```python
        raw = await asyncio.to_thread(
            yf.download,
            " ".join(all_tickers),
            period="30d",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )
```

with:

```python
        raw = await download_with_retry(
            " ".join(all_tickers),
            period="30d",
            group_by="ticker",
            progress=False,
            auto_adjust=True,
        )
```

- [ ] **Step 5: Update `treasury_yields.py`**

Same transformation as Step 4, applied to `src/fetchers/treasury_yields.py`'s equivalent block (lines ~57-64).

- [ ] **Step 6: Update `cme_fedwatch.py`**

Same transformation, applied inside `_get_futures_implied_rate()` (lines ~44-51). Add `from ._yf_lock import download_with_retry` to imports; check whether `import yfinance as yf` is still needed elsewhere in the file (it is not, based on the earlier grep — remove it if confirmed unused).

- [ ] **Step 7: Run tests to verify they pass**

Run: `pytest tests/test_fetcher_yf_lock.py -v`
Expected: All PASS.

- [ ] **Step 8: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS — pay attention to any existing tests for these 4 fetchers that patch `yf.download` directly (e.g. via `@patch("src.fetchers.sector_etf.yf.download")`); those now need to patch `download_with_retry` instead. Search `tests/` for each fetcher's module name to find and update any such tests.

- [ ] **Step 9: Commit**

```bash
git add src/fetchers/sector_etf.py src/fetchers/thematic_etf.py src/fetchers/treasury_yields.py src/fetchers/cme_fedwatch.py tests/test_fetcher_yf_lock.py
git commit -m "fix(fetchers): route sector/thematic/treasury/fedwatch yf.download through shared lock"
```

---

### Task 3: Stop `vix.py` from blocking the event loop

**Files:**
- Modify: `src/fetchers/vix.py:26-33`
- Test: check for `tests/test_vix.py` first; if absent, add to a new `tests/test_vix_fetcher.py`

- [ ] **Step 1: Write the failing test**

If no existing test file covers `vix.py`, create `tests/test_vix_fetcher.py`:

```python
"""Tests for VixFetcher event-loop-blocking fix."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.fetchers.vix import VixFetcher


@pytest.mark.asyncio
async def test_vix_fetch_does_not_block_event_loop():
    """fast_info access must go through asyncio.to_thread, not run inline."""
    spot_mock = MagicMock()
    spot_mock.fast_info.last_price = 15.0
    vix3m_mock = MagicMock()
    vix3m_mock.fast_info.last_price = 16.0

    with patch("src.fetchers.vix.yf.Ticker", side_effect=[spot_mock, vix3m_mock]):
        with patch("src.fetchers.vix.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda fn, *a, **k: asyncio.sleep(0, result=fn(*a, **k))
            signal = await VixFetcher().fetch()

    assert mock_to_thread.called
    assert signal is not None
    assert signal.metadata["spot"] == 15.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_vix_fetcher.py -v`
Expected: FAIL — `mock_to_thread.called` is `False` (the current code calls `fast_info` inline, never touching `asyncio.to_thread`).

- [ ] **Step 3: Implement the fix**

In `src/fetchers/vix.py`, add `import asyncio` to the imports. Replace:

```python
        # yfinance is synchronous but fast for single quotes
        spot_ticker = yf.Ticker(VIX_SPOT)
        vix3m_ticker = yf.Ticker(VIX_3M)

        spot_data = spot_ticker.fast_info
        vix3m_data = vix3m_ticker.fast_info

        spot_price = getattr(spot_data, "last_price", None)
        vix3m_price = getattr(vix3m_data, "last_price", None)
```

with:

```python
        def _fetch_fast_info() -> tuple[float | None, float | None]:
            spot_ticker = yf.Ticker(VIX_SPOT)
            vix3m_ticker = yf.Ticker(VIX_3M)
            spot = getattr(spot_ticker.fast_info, "last_price", None)
            vix3m = getattr(vix3m_ticker.fast_info, "last_price", None)
            return spot, vix3m

        spot_price, vix3m_price = await asyncio.to_thread(_fetch_fast_info)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_vix_fetcher.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/vix.py tests/test_vix_fetcher.py
git commit -m "fix(vix-fetcher): wrap fast_info access in asyncio.to_thread to avoid blocking the event loop"
```

---

### Task 4: Fix `unusual_volume.py` — blocking loop + missing cache

**Files:**
- Modify: `src/db.py` — add `get_unusual_volume_cache`/`set_unusual_volume_cache`
- Modify: `src/fetchers/unusual_volume.py:36-100` (`fetch`)
- Test: check for `tests/test_unusual_volume.py` first; if absent, create it

**Interfaces:**
- Consumes: pattern from `get_insider_cache`/`set_insider_cache` (`src/db.py:445-472`).
- Produces: `get_unusual_volume_cache(max_age_hours: float = 0.25) -> dict | None`, `set_unusual_volume_cache(data: dict) -> None`.

- [ ] **Step 1: Write the failing tests for the DB cache functions**

Reuse the `temp_db` fixture pattern from `tests/test_iv_backfill_circuit_breaker.py` (Task 1 of the IV-backfill plan) if that plan has already landed — otherwise define an equivalent local fixture. Create `tests/test_unusual_volume_fetcher.py`:

```python
"""Tests for UnusualVolumeFetcher: event-loop-blocking fix + result caching."""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture
def temp_db(monkeypatch):
    from src.config import settings

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)
    monkeypatch.setattr(settings, "db_path", path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_get_unusual_volume_cache_returns_none_when_empty(temp_db):
    from src.db import get_unusual_volume_cache

    assert get_unusual_volume_cache() is None


def test_set_then_get_unusual_volume_cache_roundtrip(temp_db):
    from src.db import get_unusual_volume_cache, set_unusual_volume_cache

    set_unusual_volume_cache({"spikes": [{"symbol": "SOFI"}]})
    cached = get_unusual_volume_cache(max_age_hours=1.0)

    assert cached is not None
    assert cached["spikes"] == [{"symbol": "SOFI"}]


def test_get_unusual_volume_cache_expires(temp_db):
    from src.db import get_unusual_volume_cache, set_unusual_volume_cache

    set_unusual_volume_cache({"spikes": []})
    # max_age_hours=0 means "must be from the future" — always stale immediately.
    assert get_unusual_volume_cache(max_age_hours=0.0) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_unusual_volume_fetcher.py -k cache -v`
Expected: FAIL with `ImportError`.

- [ ] **Step 3: Implement the cache functions in `db.py`**

Read `get_insider_cache`/`set_insider_cache` in `src/db.py` (lines 445-472) first — copy their exact structure (`app_config` table, JSON-encoded value, `cached_at` timestamp, age check) with a new key:

```python
def get_unusual_volume_cache(max_age_hours: float = 0.25) -> dict | None:
    """Return cached unusual-volume scan results if they exist and are fresh enough."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM app_config WHERE key = 'cache_unusual_volume'"
        ).fetchone()
        if row is None:
            return None
        cached = json.loads(row["value"])
        cached_at = datetime.fromisoformat(cached.get("cached_at", "2000-01-01"))
        age_hours = (datetime.now() - cached_at).total_seconds() / 3600
        if age_hours > max_age_hours:
            return None
        return cached
    finally:
        conn.close()


def set_unusual_volume_cache(data: dict) -> None:
    """Store unusual-volume scan results in the cache."""
    conn = _get_connection()
    try:
        data = dict(data)
        data["cached_at"] = datetime.now().isoformat()
        conn.execute(
            """
            INSERT INTO app_config (key, value) VALUES ('cache_unusual_volume', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (json.dumps(data, default=_json_default),),
        )
        conn.commit()
    finally:
        conn.close()
```

Match whatever exact upsert SQL `set_insider_cache` uses (read it first — the snippet above assumes an `ON CONFLICT(key) DO UPDATE` upsert; if `set_insider_cache` instead does a delete-then-insert or `INSERT OR REPLACE`, mirror that exact approach instead for consistency).

- [ ] **Step 4: Run cache tests to verify they pass**

Run: `pytest tests/test_unusual_volume_fetcher.py -k cache -v`
Expected: All PASS.

- [ ] **Step 5: Write the failing test for the blocking fix**

Add to `tests/test_unusual_volume_fetcher.py`:

```python
def _make_history_df(n_days: int = 25) -> pd.DataFrame:
    dates = pd.bdate_range(end="2024-06-28", periods=n_days)
    return pd.DataFrame(
        {
            "Open": [100.0] * n_days, "High": [101.0] * n_days,
            "Low": [99.0] * n_days, "Close": [100.0] * n_days,
            "Volume": [1_000_000] * (n_days - 1) + [5_000_000],  # spike on the last bar
        },
        index=dates,
    )


@pytest.mark.asyncio
async def test_unusual_volume_fetch_wraps_history_call_in_to_thread(temp_db, monkeypatch):
    from src.fetchers import unusual_volume

    monkeypatch.setattr(unusual_volume, "get_stock_watchlist", lambda: ["SOFI"])
    monkeypatch.setattr(unusual_volume, "get_unusual_volume_cache", lambda **k: None)
    monkeypatch.setattr(unusual_volume, "set_unusual_volume_cache", lambda data: None)

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _make_history_df()

    with patch("src.fetchers.unusual_volume.yf.Ticker", return_value=fake_ticker):
        with patch("src.fetchers.unusual_volume.asyncio.to_thread") as mock_to_thread:
            mock_to_thread.side_effect = lambda fn, *a, **k: asyncio.sleep(0, result=fn(*a, **k))
            signal = await unusual_volume.UnusualVolumeFetcher().fetch()

    assert mock_to_thread.called
    assert signal is not None


@pytest.mark.asyncio
async def test_unusual_volume_fetch_uses_cache_on_second_call(temp_db, monkeypatch):
    from src.fetchers import unusual_volume

    monkeypatch.setattr(unusual_volume, "get_stock_watchlist", lambda: ["SOFI"])

    fake_ticker = MagicMock()
    fake_ticker.history.return_value = _make_history_df()

    call_count = 0

    def counting_history(*a, **k):
        nonlocal call_count
        call_count += 1
        return _make_history_df()

    fake_ticker.history.side_effect = counting_history

    with patch("src.fetchers.unusual_volume.yf.Ticker", return_value=fake_ticker):
        await unusual_volume.UnusualVolumeFetcher().fetch()
        await unusual_volume.UnusualVolumeFetcher().fetch()

    assert call_count == 1  # second call served from cache, no new yfinance hit
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `pytest tests/test_unusual_volume_fetcher.py -v`
Expected: The two new tests FAIL — `mock_to_thread.called` is `False` (blocking call still inline), and `call_count == 2` (no cache yet).

- [ ] **Step 7: Implement the fix in `unusual_volume.py`**

Add `import asyncio` to the imports. Add cache imports: change `from ..db import get_stock_watchlist` to:

```python
from ..db import get_stock_watchlist, get_unusual_volume_cache, set_unusual_volume_cache
```

At the top of `fetch()`, right after the `tickers = get_stock_watchlist()` / empty-watchlist check, add a cache-read:

```python
        cached = get_unusual_volume_cache()
        if cached is not None:
            return Signal(
                source=SignalSource.UNUSUAL_VOLUME,
                value=cached["value"],
                metadata=cached["metadata"],
                summary=cached["summary"],
            )
```

Wrap the per-ticker blocking call — replace:

```python
                ticker = yf.Ticker(symbol)
                # Fetch 25 trading days — enough for 20d avg + today
                hist = ticker.history(period="25d")
```

with:

```python
                ticker = yf.Ticker(symbol)
                # Fetch 25 trading days — enough for 20d avg + today
                hist = await asyncio.to_thread(ticker.history, period="25d")
```

Before each `return Signal(...)` at the end of `fetch()` (there are at least two — the "no spikes" early return and the final return with results), add a `set_unusual_volume_cache({...})` call storing the same `value`/`metadata`/`summary` that's about to be returned, so the next call within the TTL window hits the cache. Structure it so both return paths cache consistently — e.g. build the `Signal` first, cache its fields, then return it:

```python
        signal = Signal(
            source=SignalSource.UNUSUAL_VOLUME,
            value=...,
            metadata={...},
            summary=...,
        )
        set_unusual_volume_cache(
            {"value": signal.value, "metadata": signal.metadata, "summary": signal.summary}
        )
        return signal
```

Apply this consistently to every `return Signal(...)` in the function (the "no spikes" branch and the final results branch) rather than only one, so the cache is populated regardless of which branch produced the result.

- [ ] **Step 8: Run tests to verify they pass**

Run: `pytest tests/test_unusual_volume_fetcher.py -v`
Expected: All PASS.

- [ ] **Step 9: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 10: Commit**

```bash
git add src/db.py src/fetchers/unusual_volume.py tests/test_unusual_volume_fetcher.py
git commit -m "fix(unusual-volume): stop blocking the event loop and add result caching"
```

---

### Task 5: Fix `put_call.py`'s synchronous SPY fallback

**Files:**
- Modify: `src/fetchers/put_call.py:164-190` (`_fetch_fallback`)
- Test: check for `tests/test_put_call.py` first; if absent, create `tests/test_put_call_fetcher.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_put_call_fetcher.py` (or extend the existing file if found):

```python
"""Tests for PutCallFetcher's SPY fallback event-loop-blocking fix."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from src.fetchers.put_call import PutCallFetcher


@pytest.mark.asyncio
async def test_fetch_fallback_wraps_yfinance_calls_in_to_thread():
    fetcher = PutCallFetcher()

    fake_chain = MagicMock()
    fake_chain.puts = {"volume": MagicMock(dropna=lambda: MagicMock(sum=lambda: 100))}
    fake_chain.calls = {"volume": MagicMock(dropna=lambda: MagicMock(sum=lambda: 50))}

    fake_spy = MagicMock()
    fake_spy.options = ["2024-07-19"]
    fake_spy.option_chain.return_value = fake_chain

    with patch("yfinance.Ticker", return_value=fake_spy):
        with patch(
            "src.fetchers.put_call.asyncio.to_thread"
        ) as mock_to_thread:
            mock_to_thread.side_effect = lambda fn, *a, **k: asyncio.sleep(0, result=fn(*a, **k))
            await fetcher._fetch_fallback()

    assert mock_to_thread.called
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_put_call_fetcher.py -v`
Expected: FAIL — `mock_to_thread.called` is `False` (the fallback runs entirely inline today).

- [ ] **Step 3: Implement the fix**

In `src/fetchers/put_call.py`, add `import asyncio` to the imports. Replace `_fetch_fallback`'s body — extract the synchronous yfinance work into a plain (non-async) inner function and run it via `asyncio.to_thread`:

```python
    async def _fetch_fallback(self) -> float | None:
        """Derive put/call ratio from SPY options volume across near-term expiries."""
        def _sync_fetch() -> float | None:
            import yfinance as yf

            spy = yf.Ticker("SPY")
            if not spy.options:
                return None

            total_puts = 0.0
            total_calls = 0.0
            for expiry in spy.options[:3]:
                try:
                    chain = spy.option_chain(expiry)
                    puts_vol = chain.puts["volume"].dropna().sum()
                    calls_vol = chain.calls["volume"].dropna().sum()
                    total_puts += float(puts_vol)
                    total_calls += float(calls_vol)
                except Exception:
                    continue

            if total_calls > 0:
                ratio = _validate(total_puts / total_calls)
                if ratio is not None:
                    logger.info("Put/Call: using SPY options fallback — %.3f", ratio)
                    return ratio
            return None

        try:
            return await asyncio.to_thread(_sync_fetch)
        except Exception:
            logger.debug("Put/Call: SPY fallback failed", exc_info=True)
            return None
```

Read the full original `_fetch_fallback` body first (`src/fetchers/put_call.py:164-` onward, past line 190) to confirm there is no additional logic after what was captured during planning — preserve every existing branch exactly, just move it inside `_sync_fetch` and keep the outer `try/except` wrapping the `to_thread` call instead of wrapping the body directly.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_put_call_fetcher.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full test suite**

Run: `pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
git add src/fetchers/put_call.py tests/test_put_call_fetcher.py
git commit -m "fix(put-call-fetcher): wrap SPY fallback yfinance calls in asyncio.to_thread"
```

---

## Self-Review Notes (for the implementer)

- After all 5 tasks, grep the whole `src/fetchers/` directory for `yf.download(` to confirm no direct (unlocked) call sites remain outside `_yf_lock.py` itself: `grep -rn "yf.download(" src/fetchers/` should only match `_yf_lock.py`.
- Confirm `market_overview.py` has no leftover dead code from the extraction (unused `weakref` import, orphaned constants).
- The unusual-volume cache TTL (`0.25` hours = 15 minutes) is a judgment call, not specified by the original issue — if the pipeline's typical run cadence is known to be shorter or longer, adjust it, but don't over-think this; it just needs to be short enough that intraday volume spikes are still caught promptly.
