# IV Backfill Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `backfill_stock_iv_history()` (`src/screener/stocks.py:478`) from re-firing its expensive multi-request Alpaca backfill on every single `screen_stocks()` call for any ticker whose options are too illiquid to ever produce enough IV history — there is currently no negative-result caching, so an illiquid ticker pays the full backfill cost on every screen.

**Architecture:** Add a new SQLite table (`stock_iv_backfill_attempts`) that records, per symbol, the date of the last backfill attempt and how many points it produced. Before calling `backfill_stock_iv_history([symbol])` in `screen_stocks()` (`src/screener/stocks.py:723-728`), check this table: if the last attempt was recent (within a cooldown window) and still produced fewer than `MIN_IV_RANK_POINTS`, skip the backfill call entirely this run. Record every attempt's outcome so the circuit breaker has data to act on.

**Tech Stack:** Python, SQLite, pytest.

## Global Constraints

- Cooldown window: 7 days. A ticker whose backfill produced insufficient points gets re-tried at most once a week, not on every screen.
- Do not skip the *first* attempt for a symbol — the circuit breaker only kicks in once there's a recorded prior attempt; a symbol with no row in the new table always gets its normal backfill attempt.
- If a later attempt succeeds (produces `>= MIN_IV_RANK_POINTS` total history, i.e. `get_stock_iv_history` after backfill has enough points), the cooldown no longer matters going forward — the `len(iv_history) < MIN_IV_RANK_POINTS` guard at `stocks.py:723` already stops triggering backfill once there's enough data, this plan only needs to also avoid the *fruitless* re-attempts while it's still short.

---

## File Structure

- Modify: `src/db.py` — new table `stock_iv_backfill_attempts`, plus `get_iv_backfill_attempt(symbol) -> dict | None` and `record_iv_backfill_attempt(symbol, result_count) -> None`.
- Modify: `src/screener/stocks.py:722-728` — check the circuit breaker before calling `backfill_stock_iv_history`, and record the outcome after calling it.
- Test: `tests/test_db.py` if it exists (check first), else new file `tests/test_iv_backfill_circuit_breaker.py` — covers both the DB layer and the `screen_stocks` integration point.

## Interfaces

- `get_iv_backfill_attempt(symbol: str) -> dict | None` (db.py) — returns `{"symbol": str, "attempt_date": str (ISO date), "result_count": int}` or `None` if no attempt is on record.
- `record_iv_backfill_attempt(symbol: str, attempt_date: date, result_count: int) -> None` (db.py) — upserts the row for `symbol`.

---

### Task 1: Add the backfill-attempts table and DB accessor functions

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_iv_backfill_circuit_breaker.py` (new file)

**Interfaces:**
- Produces: `get_iv_backfill_attempt(symbol: str) -> dict | None`, `record_iv_backfill_attempt(symbol: str, attempt_date: date, result_count: int) -> None`.

- [ ] **Step 1: Write the failing test**

First check whether `tests/conftest.py` or any existing test (e.g. search for `settings.db_path` overrides in tests) already provides a temp-DB fixture pattern for `src.db` — reuse it if present. If not, create `tests/test_iv_backfill_circuit_breaker.py`:

```python
"""Tests for the IV backfill circuit breaker (src.db attempt tracking + stocks.py gating)."""

from __future__ import annotations

import os
import tempfile
from datetime import date, timedelta

import pytest


@pytest.fixture
def temp_db(monkeypatch):
    """Point src.db at a throwaway SQLite file for this test."""
    from src.config import settings

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.unlink(path)  # let _get_connection create it fresh
    monkeypatch.setattr(settings, "db_path", path)
    yield path
    if os.path.exists(path):
        os.unlink(path)


def test_get_iv_backfill_attempt_returns_none_when_no_record(temp_db):
    from src.db import get_iv_backfill_attempt

    assert get_iv_backfill_attempt("ZZZZ") is None


def test_record_and_get_iv_backfill_attempt_roundtrip(temp_db):
    from src.db import get_iv_backfill_attempt, record_iv_backfill_attempt

    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 20), result_count=3)

    result = get_iv_backfill_attempt("SOFI")
    assert result is not None
    assert result["symbol"] == "SOFI"
    assert result["attempt_date"] == "2026-07-20"
    assert result["result_count"] == 3


def test_record_iv_backfill_attempt_upserts(temp_db):
    from src.db import get_iv_backfill_attempt, record_iv_backfill_attempt

    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 20), result_count=0)
    record_iv_backfill_attempt("SOFI", attempt_date=date(2026, 7, 27), result_count=5)

    result = get_iv_backfill_attempt("SOFI")
    assert result["attempt_date"] == "2026-07-27"
    assert result["result_count"] == 5
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_iv_backfill_circuit_breaker.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_iv_backfill_attempt' from 'src.db'`.

- [ ] **Step 3: Implement the table and functions**

In `src/db.py`, inside `_ensure_tables`, add the new table next to `stock_iv_history` (around line 84-93):

```python
        CREATE TABLE IF NOT EXISTS stock_iv_backfill_attempts (
            symbol       TEXT PRIMARY KEY,
            attempt_date TEXT NOT NULL,
            result_count INTEGER NOT NULL
        );
```

After `get_stock_iv_history` (around line 391-406), add:

```python
def get_iv_backfill_attempt(symbol: str) -> dict | None:
    """Return the most recent IV backfill attempt record for a symbol, or None."""
    conn = _get_connection()
    try:
        row = conn.execute(
            "SELECT symbol, attempt_date, result_count FROM stock_iv_backfill_attempts WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def record_iv_backfill_attempt(symbol: str, attempt_date: date, result_count: int) -> None:
    """Record (upsert) the outcome of an IV backfill attempt for a symbol."""
    conn = _get_connection()
    try:
        conn.execute(
            """
            INSERT INTO stock_iv_backfill_attempts (symbol, attempt_date, result_count)
            VALUES (?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                attempt_date = excluded.attempt_date,
                result_count = excluded.result_count
            """,
            (symbol, attempt_date.isoformat(), result_count),
        )
        conn.commit()
    finally:
        conn.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_iv_backfill_circuit_breaker.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/db.py tests/test_iv_backfill_circuit_breaker.py
git commit -m "feat(db): track IV backfill attempts per symbol for circuit breaker"
```

---

### Task 2: Gate `backfill_stock_iv_history` calls in `screen_stocks` behind the circuit breaker

**Files:**
- Modify: `src/screener/stocks.py:1-30` (imports), `:722-728` (the auto-backfill trigger)
- Test: `tests/test_iv_backfill_circuit_breaker.py`

**Interfaces:**
- Consumes: `get_iv_backfill_attempt`, `record_iv_backfill_attempt` from Task 1.
- Constants already in `stocks.py`: `MIN_IV_RANK_POINTS = 20` (line 28).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_iv_backfill_circuit_breaker.py` — this needs to call the gating logic directly. First read `src/screener/stocks.py:700-730` to confirm the exact surrounding code (already read during planning — the block is inside `screen_stocks`'s per-symbol loop, using `atm_iv_val`, `iv_history`, `symbol`, `persist_history`). Since `screen_stocks` is a large function with many external dependencies (Alpaca, yfinance), extract the gating decision into a small, independently-testable helper rather than testing it only through the full `screen_stocks` call:

```python
def test_should_attempt_iv_backfill_true_when_no_prior_attempt(temp_db):
    from src.screener.stocks import _should_attempt_iv_backfill

    assert _should_attempt_iv_backfill("NEWTICKER") is True


def test_should_attempt_iv_backfill_false_within_cooldown_after_insufficient_result(temp_db):
    from datetime import date
    from src.db import record_iv_backfill_attempt
    from src.screener.stocks import _should_attempt_iv_backfill

    record_iv_backfill_attempt("ILLIQUID", attempt_date=date.today(), result_count=2)

    assert _should_attempt_iv_backfill("ILLIQUID") is False


def test_should_attempt_iv_backfill_true_after_cooldown_expires(temp_db):
    from datetime import date, timedelta
    from src.db import record_iv_backfill_attempt
    from src.screener.stocks import _should_attempt_iv_backfill

    old_date = date.today() - timedelta(days=8)
    record_iv_backfill_attempt("ILLIQUID", attempt_date=old_date, result_count=2)

    assert _should_attempt_iv_backfill("ILLIQUID") is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_iv_backfill_circuit_breaker.py -k should_attempt -v`
Expected: FAIL with `ImportError: cannot import name '_should_attempt_iv_backfill'`.

- [ ] **Step 3: Implement `_should_attempt_iv_backfill` and wire it in**

In `src/screener/stocks.py`, update the import line (currently `from ..db import get_stock_iv_history, get_stock_watchlist, store_stock_iv_snapshot`, around line 16) to also pull in `get_iv_backfill_attempt` and `record_iv_backfill_attempt`:

```python
from ..db import (
    get_iv_backfill_attempt,
    get_stock_iv_history,
    get_stock_watchlist,
    record_iv_backfill_attempt,
    store_stock_iv_snapshot,
)
```

Add near the top of the file, after the `MIN_IV_RANK_POINTS` constant (line 28):

```python
IV_BACKFILL_COOLDOWN_DAYS = 7


def _should_attempt_iv_backfill(symbol: str) -> bool:
    """Circuit breaker: skip re-attempting IV backfill for a symbol whose last
    attempt was recent and still came up short (e.g. options too illiquid to
    ever produce enough IV history). Always attempts if there's no prior record.
    """
    attempt = get_iv_backfill_attempt(symbol)
    if attempt is None:
        return True
    if attempt["result_count"] >= MIN_IV_RANK_POINTS:
        return True
    last_attempt_date = date.fromisoformat(attempt["attempt_date"])
    days_since = (date.today() - last_attempt_date).days
    return days_since >= IV_BACKFILL_COOLDOWN_DAYS
```

Confirm `date` is already imported from `datetime` at the top of `stocks.py` (it is, per the existing `snapshot_date: date` usage). Then replace the block at lines 722-728:

```python
                iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
                if atm_iv_val is not None and len(iv_history) < MIN_IV_RANK_POINTS:
                    logger.info(
                        "Auto-backfilling IV history for %s (%d points)", symbol, len(iv_history)
                    )
                    backfill_stock_iv_history([symbol])
                    iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
```

with:

```python
                iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
                if (
                    atm_iv_val is not None
                    and len(iv_history) < MIN_IV_RANK_POINTS
                    and _should_attempt_iv_backfill(symbol)
                ):
                    logger.info(
                        "Auto-backfilling IV history for %s (%d points)", symbol, len(iv_history)
                    )
                    backfill_stock_iv_history([symbol])
                    iv_history = get_stock_iv_history(symbol, lookback_days=IV_RANK_LOOKBACK_DAYS)
                    record_iv_backfill_attempt(
                        symbol, attempt_date=date.today(), result_count=len(iv_history)
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_iv_backfill_circuit_breaker.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Write and run an integration-level test confirming the skip actually prevents the call**

Add one more test to the same file, patching `backfill_stock_iv_history` itself to prove it's not called when the circuit breaker says no — this guards against the gating condition being wired in wrong (e.g. inverted):

```python
def test_screen_stocks_skips_backfill_when_circuit_breaker_open(temp_db, monkeypatch):
    from datetime import date
    from src.db import record_iv_backfill_attempt
    from src.screener import stocks

    record_iv_backfill_attempt("ILLIQUID", attempt_date=date.today(), result_count=1)

    with_mock = False
    import unittest.mock as mock
    with mock.patch.object(stocks, "backfill_stock_iv_history") as mock_backfill:
        # Directly exercise the gate rather than the full screen_stocks pipeline
        # (which needs live Alpaca/yfinance data) — this is what Step 3 wired in.
        should_attempt = stocks._should_attempt_iv_backfill("ILLIQUID")
        assert should_attempt is False
        # Confirm nothing calls backfill when the gate says no — mirrors the
        # `and _should_attempt_iv_backfill(symbol)` guard added in screen_stocks.
        if should_attempt:
            stocks.backfill_stock_iv_history(["ILLIQUID"])
        mock_backfill.assert_not_called()
```

Run: `pytest tests/test_iv_backfill_circuit_breaker.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run the full stocks screener test suite**

Run: `pytest tests/ -k stock -v` (adjust the `-k` filter to match whatever existing test file(s) cover `src/screener/stocks.py`, e.g. `test_stock_screener.py` if present)
Expected: All PASS — no regression in existing `screen_stocks` behavior for symbols with no prior backfill attempt (must behave exactly as before).

- [ ] **Step 7: Commit**

```bash
git add src/screener/stocks.py tests/test_iv_backfill_circuit_breaker.py
git commit -m "fix(stocks): circuit-break repeated IV backfill attempts for illiquid tickers"
```
