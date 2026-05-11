# Balance Sheet & Profitability Filters — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add five new hard-gate fundamental filters (FCF, D/E ratio, revenue growth, earnings growth, dividend yield) to the CSP universe scanner, backed by pre-stored data in `universe_fundamentals`.

**Architecture:** Expand the `universe_fundamentals` SQLite table with five new columns, fetched during the existing nightly `market-data-refresh` via the same `ticker.info` call. New `ScannerParams` fields gate the fundamental filter stage with pass-through semantics for missing data. All layers touched: store → refresh → scanner → API → frontend.

**Tech Stack:** Python 3.12, SQLite (WAL), yfinance, FastAPI, vanilla JS (no build step)

---

## File Map

| File | What changes |
|---|---|
| `src/market_data/store.py` | DDL new columns, migration logic, upsert SQL, read SELECT statements |
| `src/market_data/refresh.py` | `_fetch_fundamentals_batch` extracts 5 new yfinance fields |
| `src/screener/csp_scanner.py` | 5 DEFAULT constants, 5 `ScannerParams` fields, `from_query()`, gate logic in `_fundamental_filter_from_store` |
| `src/api/main.py` | 5 new params on GET + DELETE `/api/screener/csp-scan` |
| `src/web/scanner.js` | `_state.params`, `PARAM_CONFIG`, `_buildQueryString`, `_restoreParams` |
| `tests/test_market_data_store.py` | Tests for new columns round-trip |
| `tests/test_fundamental_filter.py` | New file — gate logic unit tests |

---

## Task 1: Schema migration + store read/write

**Files:**
- Modify: `src/market_data/store.py`
- Test: `tests/test_market_data_store.py`

- [ ] **Step 1: Write failing tests for new fundamental columns**

Add this class to `tests/test_market_data_store.py` (after `TestFundamentalsUpsert`):

```python
class TestFundamentalsNewColumns:
    def test_new_columns_upsert_and_read(self):
        ensure_tables()
        rows = [{
            "symbol": "NEWCOL",
            "market_cap_b": 50.0,
            "price": 100.0,
            "beta": 1.0,
            "iv_pct": 25.0,
            "fcf": 5.0,               # $5B FCF
            "debt_to_equity": 0.8,
            "revenue_growth": 0.12,   # 12% growth
            "earnings_growth": 0.08,
            "dividend_yield": 0.015,
        }]
        count = bulk_upsert_fundamentals(rows)
        assert count == 1

        result = get_fundamentals_for_tickers(["NEWCOL"])
        assert len(result) == 1
        r = result[0]
        assert r["fcf"] == pytest.approx(5.0)
        assert r["debt_to_equity"] == pytest.approx(0.8)
        assert r["revenue_growth"] == pytest.approx(0.12)
        assert r["earnings_growth"] == pytest.approx(0.08)
        assert r["dividend_yield"] == pytest.approx(0.015)

    def test_new_columns_default_to_none_when_omitted(self):
        ensure_tables()
        rows = [{"symbol": "OLDSTYLE", "market_cap_b": 10.0, "price": 50.0, "beta": 1.0, "iv_pct": None}]
        bulk_upsert_fundamentals(rows)

        result = get_fundamentals_for_tickers(["OLDSTYLE"])
        assert len(result) == 1
        r = result[0]
        assert r["fcf"] is None
        assert r["debt_to_equity"] is None
        assert r["revenue_growth"] is None

    def test_ensure_tables_is_idempotent_with_migration(self):
        # Calling ensure_tables() twice should not raise
        ensure_tables()
        ensure_tables()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_market_data_store.py::TestFundamentalsNewColumns -v
```

Expected: FAIL — columns don't exist yet.

- [ ] **Step 3: Update the DDL in `src/market_data/store.py`**

Replace lines 43–50 (the `universe_fundamentals` CREATE TABLE block):

```python
CREATE TABLE IF NOT EXISTS universe_fundamentals (
    symbol          TEXT PRIMARY KEY,
    market_cap_b    REAL,
    price           REAL,
    beta            REAL,
    iv_pct          REAL,
    fcf             REAL,
    debt_to_equity  REAL,
    revenue_growth  REAL,
    earnings_growth REAL,
    dividend_yield  REAL,
    updated_at      TEXT NOT NULL
);
```

- [ ] **Step 4: Add migration logic to `ensure_tables()` in `src/market_data/store.py`**

Replace the current `ensure_tables` function (lines 63–71) with:

```python
_NEW_FUNDAMENTAL_COLUMNS = [
    "fcf REAL",
    "debt_to_equity REAL",
    "revenue_growth REAL",
    "earnings_growth REAL",
    "dividend_yield REAL",
]

def ensure_tables() -> None:
    """Create the OHLCV and fundamentals tables if they don't exist."""
    conn = _get_connection()
    try:
        conn.executescript(_DDL)
        # Migrate existing universe_fundamentals table — SQLite has no ADD COLUMN IF NOT EXISTS
        for col_def in _NEW_FUNDAMENTAL_COLUMNS:
            try:
                conn.execute(f"ALTER TABLE universe_fundamentals ADD COLUMN {col_def}")
                conn.commit()
            except sqlite3.OperationalError:
                pass  # column already exists
        conn.commit()
        logger.info("Market data tables ensured")
    finally:
        conn.close()
```

- [ ] **Step 5: Update `bulk_upsert_fundamentals` in `src/market_data/store.py`**

Replace lines 267–306 with:

```python
def bulk_upsert_fundamentals(rows: list[dict]) -> int:
    """Upsert fundamental data rows.

    Each row dict should have: symbol, market_cap_b, price, beta, iv_pct,
    and optionally: fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield.
    Returns the number of rows upserted.
    """
    if not rows:
        return 0

    conn = _get_connection()
    try:
        now = datetime.now(timezone.utc).isoformat()
        params = [
            (
                r["symbol"],
                r.get("market_cap_b"),
                r.get("price"),
                r.get("beta"),
                r.get("iv_pct"),
                r.get("fcf"),
                r.get("debt_to_equity"),
                r.get("revenue_growth"),
                r.get("earnings_growth"),
                r.get("dividend_yield"),
                now,
            )
            for r in rows
        ]
        conn.executemany(
            """
            INSERT INTO universe_fundamentals
                (symbol, market_cap_b, price, beta, iv_pct,
                 fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                market_cap_b    = excluded.market_cap_b,
                price           = excluded.price,
                beta            = excluded.beta,
                iv_pct          = excluded.iv_pct,
                fcf             = excluded.fcf,
                debt_to_equity  = excluded.debt_to_equity,
                revenue_growth  = excluded.revenue_growth,
                earnings_growth = excluded.earnings_growth,
                dividend_yield  = excluded.dividend_yield,
                updated_at      = excluded.updated_at
            """,
            params,
        )
        conn.commit()
        return len(params)
    finally:
        conn.close()
```

- [ ] **Step 6: Update read functions to include new columns**

In `get_all_fundamentals` (line ~316), update the SELECT:

```python
rows = conn.execute(
    """SELECT symbol, market_cap_b, price, beta, iv_pct,
              fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
              updated_at
       FROM universe_fundamentals"""
).fetchall()
```

In `get_fundamentals_for_tickers` (line ~331), update the SELECT:

```python
rows = conn.execute(
    f"""SELECT symbol, market_cap_b, price, beta, iv_pct,
               fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
               updated_at
        FROM universe_fundamentals WHERE symbol IN ({placeholders})""",
    tickers,
).fetchall()
```

- [ ] **Step 7: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_market_data_store.py -v
```

Expected: ALL PASS (including new `TestFundamentalsNewColumns`).

- [ ] **Step 8: Commit**

```bash
git add src/market_data/store.py tests/test_market_data_store.py
git commit -m "feat(store): add fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield columns to universe_fundamentals"
```

---

## Task 2: Refresh pipeline — fetch new yfinance fields

**Files:**
- Modify: `src/market_data/refresh.py`

- [ ] **Step 1: Update `_fetch_fundamentals_batch` in `src/market_data/refresh.py`**

Replace lines 56–80 with:

```python
def _fetch_fundamentals_batch(symbols: list[str]) -> list[dict]:
    """Fetch fundamentals via yf.Ticker().info for a batch of symbols."""
    rows: list[dict] = []
    for symbol in symbols:
        try:
            info = yf.Ticker(symbol).info
            if info.get("quoteType", "").upper() != "EQUITY":
                continue

            market_cap_b = (_to_float(info.get("marketCap")) or 0.0) / 1e9
            price = _to_float(info.get("currentPrice") or info.get("regularMarketPrice")) or 0.0
            beta = _to_float(info.get("beta"))
            iv_raw = _to_float(info.get("impliedVolatility"))
            iv_pct = round(iv_raw * 100, 2) if iv_raw is not None else None

            # New: balance sheet / profitability fields
            fcf_raw = _to_float(info.get("freeCashflow"))
            fcf = round(fcf_raw / 1e9, 4) if fcf_raw is not None else None  # stored in billions

            debt_to_equity = _to_float(info.get("debtToEquity"))
            revenue_growth = _to_float(info.get("revenueGrowth"))
            earnings_growth = _to_float(info.get("earningsGrowth"))
            dividend_yield = _to_float(info.get("dividendYield"))

            rows.append({
                "symbol": symbol,
                "market_cap_b": round(market_cap_b, 2),
                "price": round(price, 2),
                "beta": round(beta, 2) if beta is not None else None,
                "iv_pct": iv_pct,
                "fcf": fcf,
                "debt_to_equity": round(debt_to_equity, 4) if debt_to_equity is not None else None,
                "revenue_growth": round(revenue_growth, 4) if revenue_growth is not None else None,
                "earnings_growth": round(earnings_growth, 4) if earnings_growth is not None else None,
                "dividend_yield": round(dividend_yield, 4) if dividend_yield is not None else None,
            })
        except Exception as exc:
            logger.warning("Fundamental fetch failed for %s: %s", symbol, exc)
    return rows
```

- [ ] **Step 2: Verify no test regressions**

```bash
.venv/bin/pytest tests/test_market_data_store.py -v
```

Expected: ALL PASS (refresh.py has no unit tests; store tests cover the upsert path).

- [ ] **Step 3: Commit**

```bash
git add src/market_data/refresh.py
git commit -m "feat(refresh): fetch fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield from yfinance"
```

---

## Task 3: ScannerParams — new fields and defaults

**Files:**
- Modify: `src/screener/csp_scanner.py`
- Test: `tests/test_csp_scanner_conditions.py`

- [ ] **Step 1: Write failing tests for new ScannerParams fields**

Add to `tests/test_csp_scanner_conditions.py` (after existing imports):

```python
class TestScannerParamsNewFields:
    def test_defaults_are_set(self):
        p = ScannerParams()
        assert p.min_fcf_b == 0.0
        assert p.max_debt_to_equity == 2.0
        assert p.min_revenue_growth == -0.10
        assert p.min_earnings_growth is None
        assert p.min_dividend_yield is None

    def test_from_query_maps_new_params(self):
        p = ScannerParams.from_query(
            min_fcf_b=5.0,
            max_debt_to_equity=1.5,
            min_revenue_growth=0.05,
            min_earnings_growth=-0.10,
            min_dividend_yield=0.02,
        )
        assert p.min_fcf_b == 5.0
        assert p.max_debt_to_equity == 1.5
        assert p.min_revenue_growth == 0.05
        assert p.min_earnings_growth == -0.10
        assert p.min_dividend_yield == 0.02

    def test_from_query_null_disables_gate(self):
        p = ScannerParams.from_query(
            min_fcf_b=None,
            max_debt_to_equity=None,
            min_revenue_growth=None,
        )
        assert p.min_fcf_b is None
        assert p.max_debt_to_equity is None
        assert p.min_revenue_growth is None

    def test_cache_key_differs_with_new_params(self):
        p1 = ScannerParams()
        p2 = ScannerParams(min_fcf_b=10.0)
        assert p1.cache_key_suffix() != p2.cache_key_suffix()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_csp_scanner_conditions.py::TestScannerParamsNewFields -v
```

Expected: FAIL — `min_fcf_b` not a field yet.

- [ ] **Step 3: Add DEFAULT constants to `src/screener/csp_scanner.py`**

After line 55 (`DEFAULT_MAX_ADX = 50.0`), add:

```python
DEFAULT_MIN_FCF_B:            float | None = 0.0
DEFAULT_MAX_DEBT_TO_EQUITY:   float | None = 2.0
DEFAULT_MIN_REVENUE_GROWTH:   float | None = -0.10
DEFAULT_MIN_EARNINGS_GROWTH:  float | None = None
DEFAULT_MIN_DIVIDEND_YIELD:   float | None = None
```

- [ ] **Step 4: Add new fields to the `ScannerParams` dataclass**

After line 144 (`max_adx: float = DEFAULT_MAX_ADX`), add:

```python
min_fcf_b:            float | None = DEFAULT_MIN_FCF_B
max_debt_to_equity:   float | None = DEFAULT_MAX_DEBT_TO_EQUITY
min_revenue_growth:   float | None = DEFAULT_MIN_REVENUE_GROWTH
min_earnings_growth:  float | None = DEFAULT_MIN_EARNINGS_GROWTH
min_dividend_yield:   float | None = DEFAULT_MIN_DIVIDEND_YIELD
```

- [ ] **Step 5: Update `from_query()` to accept new params**

Add 5 new keyword args to the `from_query` signature (after the existing `conditions` param):

```python
@classmethod
def from_query(
    cls,
    min_cap: float | None = None,
    max_price: float | None = None,
    min_beta: float | None = None,
    max_beta: float | None = None,
    min_vol: float | None = None,
    max_rsi: float | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    min_adx: float | None = None,
    max_adx: float | None = None,
    conditions: str | None = None,
    min_fcf_b: float | None = "MISSING",
    max_debt_to_equity: float | None = "MISSING",
    min_revenue_growth: float | None = "MISSING",
    min_earnings_growth: float | None = "MISSING",
    min_dividend_yield: float | None = "MISSING",
) -> "ScannerParams":
```

And in the `return cls(...)` block, add:

```python
min_fcf_b           = DEFAULT_MIN_FCF_B          if min_fcf_b          == "MISSING" else min_fcf_b,
max_debt_to_equity  = DEFAULT_MAX_DEBT_TO_EQUITY  if max_debt_to_equity == "MISSING" else max_debt_to_equity,
min_revenue_growth  = DEFAULT_MIN_REVENUE_GROWTH  if min_revenue_growth == "MISSING" else min_revenue_growth,
min_earnings_growth = DEFAULT_MIN_EARNINGS_GROWTH if min_earnings_growth == "MISSING" else min_earnings_growth,
min_dividend_yield  = DEFAULT_MIN_DIVIDEND_YIELD  if min_dividend_yield  == "MISSING" else min_dividend_yield,
```

> Note: `"MISSING"` sentinel distinguishes "caller passed None (gate disabled)" from "caller omitted the param (use default)". This is needed because both the default and the disabled state can be `None` for the last two fields.

- [ ] **Step 6: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_csp_scanner_conditions.py::TestScannerParamsNewFields -v
```

Expected: ALL PASS.

- [ ] **Step 7: Run full test suite to check regressions**

```bash
.venv/bin/pytest tests/test_csp_scanner_conditions.py tests/test_market_data_store.py -v
```

Expected: ALL PASS.

- [ ] **Step 8: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_conditions.py
git commit -m "feat(scanner): add min_fcf_b, max_debt_to_equity, min_revenue_growth, min_earnings_growth, min_dividend_yield to ScannerParams"
```

---

## Task 4: Fundamental filter gate logic

**Files:**
- Modify: `src/screener/csp_scanner.py`
- Create: `tests/test_fundamental_filter.py`

- [ ] **Step 1: Create `tests/test_fundamental_filter.py` with failing tests**

```python
"""Unit tests for the new balance sheet / profitability gate logic
in _fundamental_filter_from_store."""

from __future__ import annotations
import pytest
from unittest.mock import patch
from src.screener.csp_scanner import ScannerParams, _fundamental_filter_from_store


def _row(**kwargs) -> dict:
    """Build a minimal store row that passes all gates by default."""
    base = {
        "symbol": "TEST",
        "market_cap_b": 50.0,
        "price": 80.0,
        "beta": 1.2,
        "iv_pct": 35.0,
        "fcf": 5.0,               # $5B — passes min_fcf_b=0
        "debt_to_equity": 0.8,    # passes max_debt_to_equity=2.0
        "revenue_growth": 0.10,   # 10% — passes min_revenue_growth=-0.10
        "earnings_growth": 0.05,
        "dividend_yield": 0.02,
    }
    base.update(kwargs)
    return base


def _run(rows: list[dict], **param_kwargs) -> list[str]:
    """Run _fundamental_filter_from_store and return passing symbols."""
    params = ScannerParams(**param_kwargs)
    store_lookup = {r["symbol"]: r for r in rows}
    passing, _ = _fundamental_filter_from_store(list(store_lookup.keys()), params, store_lookup)
    return passing


class TestFcfGate:
    def test_positive_fcf_passes_default_gate(self):
        result = _run([_row(symbol="A", fcf=1.0)])
        assert "A" in result

    def test_negative_fcf_fails_default_gate(self):
        result = _run([_row(symbol="A", fcf=-1.0)])
        assert "A" not in result

    def test_none_fcf_passes_gate(self):
        result = _run([_row(symbol="A", fcf=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", fcf=-999.0)], min_fcf_b=None)
        assert "A" in result


class TestDebtToEquityGate:
    def test_low_de_passes(self):
        result = _run([_row(symbol="A", debt_to_equity=1.0)])
        assert "A" in result

    def test_high_de_fails(self):
        result = _run([_row(symbol="A", debt_to_equity=3.0)])
        assert "A" not in result

    def test_none_de_passes(self):
        result = _run([_row(symbol="A", debt_to_equity=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", debt_to_equity=999.0)], max_debt_to_equity=None)
        assert "A" in result


class TestRevenueGrowthGate:
    def test_positive_growth_passes(self):
        result = _run([_row(symbol="A", revenue_growth=0.10)])
        assert "A" in result

    def test_severe_decline_fails(self):
        # -15% is below the default -10% threshold
        result = _run([_row(symbol="A", revenue_growth=-0.15)])
        assert "A" not in result

    def test_mild_decline_passes(self):
        # -5% is above the default -10% threshold
        result = _run([_row(symbol="A", revenue_growth=-0.05)])
        assert "A" in result

    def test_none_growth_passes(self):
        result = _run([_row(symbol="A", revenue_growth=None)])
        assert "A" in result

    def test_gate_disabled_when_param_is_none(self):
        result = _run([_row(symbol="A", revenue_growth=-0.99)], min_revenue_growth=None)
        assert "A" in result


class TestEarningsGrowthGate:
    def test_gate_off_by_default(self):
        # Default is None — even terrible earnings pass
        result = _run([_row(symbol="A", earnings_growth=-0.80)])
        assert "A" in result

    def test_gate_active_when_set(self):
        result = _run([_row(symbol="A", earnings_growth=-0.50)], min_earnings_growth=-0.20)
        assert "A" not in result

    def test_none_data_passes_active_gate(self):
        result = _run([_row(symbol="A", earnings_growth=None)], min_earnings_growth=-0.20)
        assert "A" in result


class TestDividendYieldGate:
    def test_gate_off_by_default(self):
        result = _run([_row(symbol="A", dividend_yield=0.0)])
        assert "A" in result

    def test_gate_active_when_set(self):
        result = _run([_row(symbol="A", dividend_yield=0.005)], min_dividend_yield=0.02)
        assert "A" not in result

    def test_none_data_passes_active_gate(self):
        result = _run([_row(symbol="A", dividend_yield=None)], min_dividend_yield=0.02)
        assert "A" in result
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
.venv/bin/pytest tests/test_fundamental_filter.py -v
```

Expected: FAIL — gate logic not implemented yet.

- [ ] **Step 3: Add gate logic to `_fundamental_filter_from_store` in `src/screener/csp_scanner.py`**

In `_fundamental_filter_from_store` (lines 318–354), after the existing beta check (`if beta is None or not (params.min_beta <= beta <= params.max_beta): continue`), before `passing_tickers.append(symbol)`, add:

```python
        # FCF gate (stored in billions)
        fcf = row.get("fcf")
        if params.min_fcf_b is not None and fcf is not None:
            if fcf < params.min_fcf_b:
                continue

        # Debt-to-equity gate
        debt_to_equity = row.get("debt_to_equity")
        if params.max_debt_to_equity is not None and debt_to_equity is not None:
            if debt_to_equity > params.max_debt_to_equity:
                continue

        # Revenue growth gate
        revenue_growth = row.get("revenue_growth")
        if params.min_revenue_growth is not None and revenue_growth is not None:
            if revenue_growth < params.min_revenue_growth:
                continue

        # Earnings growth gate
        earnings_growth = row.get("earnings_growth")
        if params.min_earnings_growth is not None and earnings_growth is not None:
            if earnings_growth < params.min_earnings_growth:
                continue

        # Dividend yield gate
        dividend_yield = row.get("dividend_yield")
        if params.min_dividend_yield is not None and dividend_yield is not None:
            if dividend_yield < params.min_dividend_yield:
                continue
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
.venv/bin/pytest tests/test_fundamental_filter.py -v
```

Expected: ALL PASS.

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/test_csp_scanner_conditions.py tests/test_market_data_store.py tests/test_fundamental_filter.py -v
```

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_fundamental_filter.py
git commit -m "feat(scanner): add FCF, D/E, revenue growth, earnings growth, dividend yield gates to fundamental filter"
```

---

## Task 5: API endpoint — expose new params

**Files:**
- Modify: `src/api/main.py`

- [ ] **Step 1: Add new params to `get_csp_scan_candidates` (line ~304)**

Replace the function signature (lines 304–317) with:

```python
@app.get("/api/screener/csp-scan")
async def get_csp_scan_candidates(
    min_cap:              float | None = None,
    max_price:            float | None = None,
    min_beta:             float | None = None,
    max_beta:             float | None = None,
    min_vol:              float | None = None,
    max_rsi:              float | None = None,
    min_adx:              float | None = None,
    max_adx:              float | None = None,
    min_dte:              int   | None = None,
    max_dte:              int   | None = None,
    conditions:           str   | None = None,
    min_fcf_b:            float | None = "MISSING",
    max_debt_to_equity:   float | None = "MISSING",
    min_revenue_growth:   float | None = "MISSING",
    min_earnings_growth:  float | None = "MISSING",
    min_dividend_yield:   float | None = "MISSING",
):
```

> FastAPI doesn't allow a string default for `float | None` — use `Query(default="MISSING")` or handle differently. The cleaner pattern is to use a sentinel object. Replace `"MISSING"` with `Query(default=None)` for the API, and instead pass the values directly to `from_query()` which already knows its own defaults.

Revised approach — simpler API signature (FastAPI-friendly):

```python
@app.get("/api/screener/csp-scan")
async def get_csp_scan_candidates(
    min_cap:              float | None = None,
    max_price:            float | None = None,
    min_beta:             float | None = None,
    max_beta:             float | None = None,
    min_vol:              float | None = None,
    max_rsi:              float | None = None,
    min_adx:              float | None = None,
    max_adx:              float | None = None,
    min_dte:              int   | None = None,
    max_dte:              int   | None = None,
    conditions:           str   | None = None,
    min_fcf_b:            float | None = Query(default=0.0),
    max_debt_to_equity:   float | None = Query(default=2.0),
    min_revenue_growth:   float | None = Query(default=-0.10),
    min_earnings_growth:  float | None = Query(default=None),
    min_dividend_yield:   float | None = Query(default=None),
):
```

Update the import at line 10 of `main.py` to include `Query`:

```python
from fastapi import FastAPI, HTTPException, Request, BackgroundTasks, Query
```

- [ ] **Step 2: Update the `from_query()` call in `get_csp_scan_candidates` (line ~340)**

Add the 5 new args:

```python
params = ScannerParams.from_query(
    min_cap=min_cap,
    max_price=max_price,
    min_beta=min_beta,
    max_beta=max_beta,
    min_vol=min_vol,
    max_rsi=max_rsi,
    min_adx=min_adx,
    max_adx=max_adx,
    min_dte=min_dte,
    max_dte=max_dte,
    conditions=conditions,
    min_fcf_b=min_fcf_b,
    max_debt_to_equity=max_debt_to_equity,
    min_revenue_growth=min_revenue_growth,
    min_earnings_growth=min_earnings_growth,
    min_dividend_yield=min_dividend_yield,
)
```

- [ ] **Step 3: Mirror the same changes to `invalidate_csp_scan_cache` (line ~381)**

Add the same 5 params to the DELETE endpoint signature and its `from_query()` call.

- [ ] **Step 4: Fix `ScannerParams.from_query()` to not use a string sentinel**

Since the API now passes explicit defaults, simplify `from_query()` in `src/screener/csp_scanner.py`. Replace the "MISSING" sentinel approach with direct `None` checking using the fact that API defaults are pre-applied:

```python
@classmethod
def from_query(
    cls,
    min_cap: float | None = None,
    max_price: float | None = None,
    min_beta: float | None = None,
    max_beta: float | None = None,
    min_vol: float | None = None,
    max_rsi: float | None = None,
    min_dte: int | None = None,
    max_dte: int | None = None,
    min_adx: float | None = None,
    max_adx: float | None = None,
    conditions: str | None = None,
    min_fcf_b: float | None = DEFAULT_MIN_FCF_B,
    max_debt_to_equity: float | None = DEFAULT_MAX_DEBT_TO_EQUITY,
    min_revenue_growth: float | None = DEFAULT_MIN_REVENUE_GROWTH,
    min_earnings_growth: float | None = DEFAULT_MIN_EARNINGS_GROWTH,
    min_dividend_yield: float | None = DEFAULT_MIN_DIVIDEND_YIELD,
) -> "ScannerParams":
    """Build ScannerParams from API query parameters (all optional)."""
    parsed_conditions: list[str] = []
    if conditions:
        parsed_conditions = [
            c.strip() for c in conditions.split(",")
            if c.strip() in _CONDITION_IDS
        ]
    return cls(
        min_market_cap_b    = min_cap    if min_cap    is not None else DEFAULT_MIN_MARKET_CAP_B,
        max_price           = max_price  if max_price  is not None else DEFAULT_MAX_PRICE,
        min_beta            = min_beta   if min_beta   is not None else DEFAULT_MIN_BETA,
        max_beta            = max_beta   if max_beta   is not None else DEFAULT_MAX_BETA,
        min_vol_pct         = min_vol    if min_vol    is not None else DEFAULT_MIN_VOL_PCT,
        max_rsi             = max_rsi    if max_rsi    is not None else DEFAULT_MAX_RSI,
        min_dte             = int(min_dte) if min_dte is not None else DEFAULT_MIN_DTE,
        max_dte             = int(max_dte) if max_dte is not None else DEFAULT_MAX_DTE,
        min_adx             = min_adx    if min_adx    is not None else DEFAULT_MIN_ADX,
        max_adx             = max_adx    if max_adx    is not None else DEFAULT_MAX_ADX,
        conditions          = sorted(parsed_conditions),
        min_fcf_b           = min_fcf_b,
        max_debt_to_equity  = max_debt_to_equity,
        min_revenue_growth  = min_revenue_growth,
        min_earnings_growth = min_earnings_growth,
        min_dividend_yield  = min_dividend_yield,
    )
```

> The existing params keep their `None → use default` logic. The new params use their argument value directly (which is pre-defaulted by the API layer's `Query(default=...)` or by `from_query()`'s own defaults).

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/ --ignore=tests/test_stock_screener.py -v
```

Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add src/api/main.py src/screener/csp_scanner.py
git commit -m "feat(api): expose min_fcf_b, max_debt_to_equity, min_revenue_growth, min_earnings_growth, min_dividend_yield on CSP scan endpoints"
```

---

## Task 6: Frontend — new filter badges

**Files:**
- Modify: `src/web/scanner.js`

- [ ] **Step 1: Add new fields to `_state.params` (line ~23)**

In the `params` object inside `_state`, add after `conditions: []`:

```js
min_fcf_b:           0,      // FCF ≥ $0B (gate on)
max_debt_to_equity:  2.0,    // D/E ≤ 2.0 (gate on)
min_revenue_growth: -0.10,   // Rev growth ≥ -10% (gate on)
min_earnings_growth: null,   // null = gate off
min_dividend_yield:  null,   // null = gate off
```

- [ ] **Step 2: Add new entries to `PARAM_CONFIG` (line ~90)**

Add after the `dte_max` entry:

```js
{ key: 'min_fcf_b',          label: 'FCF >',     suffix: 'B',  min: -50,  max: 500,  step: 1,   decimals: 1 },
{ key: 'max_debt_to_equity',  label: 'D/E <',     suffix: '',   min: 0,    max: 20,   step: 0.1, decimals: 1 },
{ key: 'min_revenue_growth',  label: 'Rev >',     suffix: '%',  min: -100, max: 100,  step: 1,   decimals: 0, scale: 100 },
{ key: 'min_earnings_growth', label: 'EPS >',     suffix: '%',  min: -100, max: 100,  step: 1,   decimals: 0, scale: 100, nullable: true },
{ key: 'min_dividend_yield',  label: 'Div >',     suffix: '%',  min: 0,    max: 20,   step: 0.1, decimals: 1, scale: 100, nullable: true },
```

> `scale: 100` means the stored value is a decimal (e.g. `0.12`) but the badge displays and accepts input as a percentage (e.g. `12`). `nullable: true` means the param can be disabled (null = gate off).

- [ ] **Step 3: Update `renderParamBadges` to handle `scale` and `nullable`**

Find `renderParamBadges` (line ~127). Update the badge rendering to handle both `scale` and `nullable`:

```js
function renderParamBadges() {
    const container = document.getElementById('param-badges');
    if (!container) return;

    container.innerHTML = PARAM_CONFIG.map(cfg => {
        const rawVal = _state.params[cfg.key];
        const isNull = rawVal === null || rawVal === undefined;

        if (cfg.nullable && isNull) {
            return `
                <div class="param-badge param-badge--disabled" id="badge-${cfg.key}"
                     onclick="enableNullableBadge('${cfg.key}')" title="Click to enable">
                    ${cfg.label} <span class="badge-val">off</span>
                </div>`;
        }

        const displayVal = cfg.scale ? Math.round(rawVal * cfg.scale) : rawVal;
        const formatted = cfg.decimals > 0 ? Number(displayVal).toFixed(cfg.decimals) : displayVal;
        const prefix = cfg.prefix || '';
        const label = `${cfg.label} ${prefix}${formatted}${cfg.suffix}`;
        const nullBtn = cfg.nullable ? ` <span class="badge-clear" onclick="event.stopPropagation(); disableNullableBadge('${cfg.key}')">×</span>` : '';

        return `
            <div class="param-badge" id="badge-${cfg.key}" onclick="openParamEdit('${cfg.key}')">
                ${label}${nullBtn}
            </div>`;
    }).join('');
}
```

- [ ] **Step 4: Add `enableNullableBadge` and `disableNullableBadge` helpers**

After `renderParamBadges`, add:

```js
function enableNullableBadge(key) {
    const cfg = PARAM_CONFIG.find(c => c.key === key);
    if (!cfg) return;
    // Set a sensible activation default
    const activationDefaults = {
        min_earnings_growth: -0.20,
        min_dividend_yield: 0.01,
    };
    _state.params[key] = activationDefaults[key] ?? 0;
    renderParamBadges();
    _persistParams();
}

function disableNullableBadge(key) {
    _state.params[key] = null;
    renderParamBadges();
    _persistParams();
}
```

- [ ] **Step 5: Update `openParamEdit` and `commitParamEdit` to handle `scale`**

Replace `openParamEdit` (lines ~148–177) and `commitParamEdit` (lines ~184–198) with:

```js
function openParamEdit(key) {
    const cfg = PARAM_CONFIG.find(c => c.key === key);
    if (!cfg) return;

    const badge = document.getElementById(`badge-${key}`);
    if (!badge) return;

    const rawVal = _state.params[key];
    const displayVal = cfg.scale ? Math.round(rawVal * cfg.scale) : rawVal;

    badge.classList.add('editing');
    badge.innerHTML = `
        <span class="pb-label">${cfg.label}</span>
        <input
            id="param-input-${key}"
            class="pb-input"
            type="number"
            value="${displayVal}"
            min="${cfg.min}"
            max="${cfg.max}"
            step="${cfg.step}"
            onkeydown="handleParamKey(event, '${key}')"
            onblur="commitParamEdit('${key}')"
        >
        <span class="pb-suffix">${cfg.suffix}</span>
    `;

    const input = document.getElementById(`param-input-${key}`);
    if (input) { input.focus(); input.select(); }
}

function commitParamEdit(key) {
    const input = document.getElementById(`param-input-${key}`);
    if (!input) return;

    const cfg = PARAM_CONFIG.find(c => c.key === key);
    let val = parseFloat(input.value);

    if (isNaN(val)) val = cfg.scale ? Math.round(_state.params[key] * cfg.scale) : _state.params[key];
    val = Math.max(cfg.min, Math.min(cfg.max, val));

    // Scale back to stored unit (e.g. 12 → 0.12 for growth/yield percentages)
    _state.params[key] = cfg.scale ? val / cfg.scale : val;

    renderParamBadges();
    _persistParams();
}
```

- [ ] **Step 6: Update `_buildQueryString` to include new params and skip nulls**

Replace lines 297–314 with:

```js
function _buildQueryString() {
    const p = _state.params;
    const qs = new URLSearchParams({
        min_cap:   p.min_cap,
        max_price: p.max_price,
        min_beta:  p.min_beta,
        max_beta:  p.max_beta,
        min_vol:   p.min_vol,
        max_rsi:   p.rsi_max,
        min_adx:   p.adx_min,
        max_adx:   p.adx_max,
        min_dte:   p.dte_min,
        max_dte:   p.dte_max,
    });
    if (p.conditions.length) qs.set('conditions', p.conditions.join(','));
    // New params — only include when non-null (null = gate disabled)
    if (p.min_fcf_b           !== null && p.min_fcf_b           !== undefined) qs.set('min_fcf_b',           p.min_fcf_b);
    if (p.max_debt_to_equity  !== null && p.max_debt_to_equity  !== undefined) qs.set('max_debt_to_equity',  p.max_debt_to_equity);
    if (p.min_revenue_growth  !== null && p.min_revenue_growth  !== undefined) qs.set('min_revenue_growth',  p.min_revenue_growth);
    if (p.min_earnings_growth !== null && p.min_earnings_growth !== undefined) qs.set('min_earnings_growth', p.min_earnings_growth);
    if (p.min_dividend_yield  !== null && p.min_dividend_yield  !== undefined) qs.set('min_dividend_yield',  p.min_dividend_yield);
    return qs.toString();
}
```

- [ ] **Step 7: Update `_restoreParams` to restore new fields**

After the existing restore lines (line ~71), add:

```js
if (typeof saved.min_fcf_b           === 'number' || saved.min_fcf_b           === null) p.min_fcf_b           = saved.min_fcf_b;
if (typeof saved.max_debt_to_equity  === 'number' || saved.max_debt_to_equity  === null) p.max_debt_to_equity  = saved.max_debt_to_equity;
if (typeof saved.min_revenue_growth  === 'number' || saved.min_revenue_growth  === null) p.min_revenue_growth  = saved.min_revenue_growth;
if (typeof saved.min_earnings_growth === 'number' || saved.min_earnings_growth === null) p.min_earnings_growth = saved.min_earnings_growth;
if (typeof saved.min_dividend_yield  === 'number' || saved.min_dividend_yield  === null) p.min_dividend_yield  = saved.min_dividend_yield;
```

- [ ] **Step 8: Add CSS for disabled badge style**

In `src/web/index.css` (or wherever `.param-badge` is defined), add:

```css
.param-badge--disabled {
    opacity: 0.45;
    border-style: dashed;
    cursor: pointer;
}
.badge-clear {
    margin-left: 4px;
    color: var(--text-muted, #888);
    font-size: 0.85em;
    cursor: pointer;
}
.badge-clear:hover {
    color: var(--text, #333);
}
```

- [ ] **Step 9: Manual browser test**

Start the API and open the scanner page:

```bash
docker compose up api
# or: uvicorn src.api.main:app --reload --port 8000
```

Verify:
1. Three new always-on badges render: `FCF > 0B`, `D/E < 2.0`, `Rev > -10%`
2. Two disabled badges render: `EPS > off`, `Div > off`
3. Clicking a disabled badge activates it with a default value
4. Clicking the `×` on an active nullable badge disables it
5. Editing a `%` badge (Rev, EPS, Div) shows percentage values, not decimals
6. Running a scan sends the correct query string (check Network tab)
7. Page refresh restores all badge values from localStorage

- [ ] **Step 10: Commit**

```bash
git add src/web/scanner.js src/web/index.css
git commit -m "feat(web): add FCF, D/E, revenue growth, earnings growth, dividend yield filter badges to CSP scanner"
```

---

## Verification

Run these after all tasks complete:

```bash
# 1. Full test suite
.venv/bin/pytest tests/ --ignore=tests/test_stock_screener.py -v

# 2. Data refresh (verifies new columns populate)
docker compose run --rm market-data-refresh

# 3. Spot-check DB
sqlite3 data/market_intelligence.db \
  "SELECT symbol, fcf, debt_to_equity, revenue_growth FROM universe_fundamentals LIMIT 10;"

# 4. Gate tightening (fundamental pass count should drop)
curl "http://localhost:8000/api/screener/csp-scan?max_debt_to_equity=0.1" | jq .filter_summary

# 5. Absurd gate (expect 0 fundamental survivors)
curl "http://localhost:8000/api/screener/csp-scan?min_fcf_b=999999" | jq .filter_summary

# 6. Regression: all gates off
curl "http://localhost:8000/api/screener/csp-scan?min_fcf_b=null&max_debt_to_equity=null&min_revenue_growth=null" | jq .filter_summary
```
