# CSP Expanded NASDAQ Universe Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expand the CSP Universe Scanner from S&P 500 + NASDAQ 100 (~516 tickers) to all NASDAQ-listed stocks above $2B market cap (~900–1,100 tickers), with a UI toggle to filter back to the watchlist universe.

**Architecture:** Tag each ticker in `universe_fundamentals` with a `universes` TEXT column (comma-separated: `"sp500"`, `"nasdaq100"`, `"nasdaq_large"`). A new `fetch_nasdaq_large_cap_tickers()` fetcher pulls all NASDAQ stocks ≥ $2B via the NASDAQ screener API. `refresh_universe()` builds a membership map from all three lists and stamps each row. A `restrict_to_watchlist_universe` param on `ScannerParams` gates the scanner to sp500/nasdaq100 tagged rows only.

**Tech Stack:** Python 3.12, SQLite (WAL), yfinance, pandas, FastAPI, vanilla JS/HTML/CSS

---

## File Map

| File | Change |
|------|--------|
| `src/screener/csp_scanner.py` | Add `fetch_nasdaq_large_cap_tickers()`, update `fetch_universe()`, add `restrict_to_watchlist_universe` to `ScannerParams` + `from_query()`, add filter in `_fundamental_filter_from_store()` |
| `src/market_data/store.py` | Add `universes` column migration, update `bulk_upsert_fundamentals()`, `get_all_fundamentals()`, `get_fundamentals_for_tickers()` |
| `src/market_data/refresh.py` | Update `refresh_universe()` to build membership map and stamp `universes` on each fundamental row |
| `src/api/main.py` | Add `restrict_to_watchlist_universe` query param to `GET /api/screener/csp-scan` and `DELETE /api/screener/csp-scan` |
| `src/web/scanner.js` | Add state field, `_buildQueryString()` update, `_restoreParams()` update, toggle button renderer |
| `src/web/scanner.html` | Add toggle button element between `conditions-area` and `scan-controls` |
| `tests/test_market_data_store.py` | Add `universes` column tests |
| `tests/test_market_data_refresh.py` | Add membership tagging tests |
| `tests/test_csp_scanner_integration.py` | Add `restrict_to_watchlist_universe` filter tests |

---

## Task 1: Schema — add `universes` column to `universe_fundamentals`

**Files:**
- Modify: `src/market_data/store.py`
- Test: `tests/test_market_data_store.py`

- [ ] **Step 1: Write the failing test**

Add to the end of `tests/test_market_data_store.py`:

```python
def test_universes_column_exists():
    """ensure_tables() must add the universes column to universe_fundamentals."""
    ensure_tables()
    conn = sqlite3.connect(_tmp_db_path)
    try:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(universe_fundamentals)").fetchall()]
        assert "universes" in cols
    finally:
        conn.close()


def test_bulk_upsert_fundamentals_stores_universes():
    ensure_tables()
    rows = [{"symbol": "ZVZZT", "market_cap_b": 5.0, "price": 50.0, "beta": 1.0,
             "iv_pct": None, "universes": "sp500,nasdaq_large"}]
    n = bulk_upsert_fundamentals(rows)
    assert n == 1
    result = get_all_fundamentals()
    match = next((r for r in result if r["symbol"] == "ZVZZT"), None)
    assert match is not None
    assert match["universes"] == "sp500,nasdaq_large"


def test_bulk_upsert_fundamentals_defaults_empty_universes():
    ensure_tables()
    rows = [{"symbol": "ZVZZT2", "market_cap_b": 3.0, "price": 30.0, "beta": 0.9, "iv_pct": None}]
    bulk_upsert_fundamentals(rows)
    result = get_all_fundamentals()
    match = next((r for r in result if r["symbol"] == "ZVZZT2"), None)
    assert match is not None
    assert match["universes"] == ""
```

You need to add `import sqlite3` at the top of the test file if it isn't already there. Check first with grep.

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_data_store.py::test_universes_column_exists tests/test_market_data_store.py::test_bulk_upsert_fundamentals_stores_universes tests/test_market_data_store.py::test_bulk_upsert_fundamentals_defaults_empty_universes -v
```

Expected: FAIL — `universes` not in columns / `KeyError`

- [ ] **Step 3: Add column to `_NEW_FUNDAMENTAL_COLUMNS` in `src/market_data/store.py`**

Find `_NEW_FUNDAMENTAL_COLUMNS` at line ~71 and add the new entry:

```python
_NEW_FUNDAMENTAL_COLUMNS = [
    "fcf REAL",
    "debt_to_equity REAL",
    "revenue_growth REAL",
    "earnings_growth REAL",
    "dividend_yield REAL",
    "forward_pe REAL",
    "universes TEXT NOT NULL DEFAULT ''",
]
```

- [ ] **Step 4: Update `bulk_upsert_fundamentals()` to include `universes`**

Replace the `params` list comprehension and INSERT statement (~line 304–341):

```python
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
            r.get("forward_pe"),
            r.get("universes", ""),
            now,
        )
        for r in rows
    ]
    conn.executemany(
        """
        INSERT INTO universe_fundamentals
            (symbol, market_cap_b, price, beta, iv_pct,
             fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
             forward_pe, universes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            forward_pe      = excluded.forward_pe,
            universes       = excluded.universes,
            updated_at      = excluded.updated_at
        """,
        params,
    )
```

- [ ] **Step 5: Update `get_all_fundamentals()` and `get_fundamentals_for_tickers()` to select `universes`**

In `get_all_fundamentals()` (~line 351), replace the SELECT:

```python
        rows = conn.execute(
            """SELECT symbol, market_cap_b, price, beta, iv_pct,
                      fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                      forward_pe, universes, updated_at
               FROM universe_fundamentals"""
        ).fetchall()
```

In `get_fundamentals_for_tickers()` (~line 367), replace the SELECT:

```python
        rows = conn.execute(
            f"""SELECT symbol, market_cap_b, price, beta, iv_pct,
                       fcf, debt_to_equity, revenue_growth, earnings_growth, dividend_yield,
                       forward_pe, universes, updated_at
                FROM universe_fundamentals WHERE symbol IN ({placeholders})""",
            tickers,
        ).fetchall()
```

- [ ] **Step 6: Run tests to confirm passing**

```bash
.venv/bin/pytest tests/test_market_data_store.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/market_data/store.py tests/test_market_data_store.py
git commit -m "feat(store): add universes column to universe_fundamentals"
```

---

## Task 2: Universe fetcher — `fetch_nasdaq_large_cap_tickers()`

**Files:**
- Modify: `src/screener/csp_scanner.py`
- Test: `tests/test_csp_scanner_integration.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_csp_scanner_integration.py`:

```python
import respx
import httpx
import json


MOCK_NASDAQ_SCREENER_RESPONSE = {
    "data": {
        "rows": [
            {"symbol": "AAPL", "marketCap": "2700000000000"},
            {"symbol": "MSFT", "marketCap": "3000000000000"},
            {"symbol": "SMLC", "marketCap": "500000000"},   # < $2B — filtered out
            {"symbol": "SMLL", "marketCap": "1999999999"},  # < $2B — filtered out
            {"symbol": "MIDC", "marketCap": "2000000001"},  # just over $2B — kept
        ]
    }
}


@respx.mock
def test_fetch_nasdaq_large_cap_tickers_filters_by_market_cap():
    respx.get("https://api.nasdaq.com/api/screener/stocks").mock(
        return_value=httpx.Response(200, json=MOCK_NASDAQ_SCREENER_RESPONSE)
    )
    from src.screener.csp_scanner import fetch_nasdaq_large_cap_tickers
    tickers = fetch_nasdaq_large_cap_tickers()
    assert "AAPL" in tickers
    assert "MSFT" in tickers
    assert "MIDC" in tickers
    assert "SMLC" not in tickers
    assert "SMLL" not in tickers
    assert tickers == sorted(tickers)  # must be sorted


@respx.mock
def test_fetch_nasdaq_large_cap_tickers_returns_empty_on_api_failure():
    respx.get("https://api.nasdaq.com/api/screener/stocks").mock(
        return_value=httpx.Response(500)
    )
    from src.screener.csp_scanner import fetch_nasdaq_large_cap_tickers
    result = fetch_nasdaq_large_cap_tickers()
    assert result == []
```

Note: The existing tests in this file may use `unittest.mock.patch` for HTTP calls. Check if `respx` is already imported at the top; if not, add it. Confirm `respx` is in dev dependencies — it is (used in other tests).

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_csp_scanner_integration.py::test_fetch_nasdaq_large_cap_tickers_filters_by_market_cap tests/test_csp_scanner_integration.py::test_fetch_nasdaq_large_cap_tickers_returns_empty_on_api_failure -v
```

Expected: FAIL — `cannot import name 'fetch_nasdaq_large_cap_tickers'`

- [ ] **Step 3: Add the URL constant and fetcher function to `src/screener/csp_scanner.py`**

After the existing URL constants (~line 69), add:

```python
_NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&exchange=nasdaq"
_NASDAQ_LARGE_CAP_MIN_B = 2.0
```

After `fetch_nasdaq100_tickers()` (~line 276), add:

```python
def fetch_nasdaq_large_cap_tickers() -> list[str]:
    """Fetch all NASDAQ-listed stocks with market cap ≥ $2B from the NASDAQ screener API."""
    try:
        import requests as _requests
        resp = _requests.get(_NASDAQ_SCREENER_URL, headers=_NASDAQ_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("rows", [])
        if not rows:
            logger.error("NASDAQ screener API returned no rows. Keys: %s", list(data.keys()))
            return []

        tickers: list[str] = []
        min_cap = _NASDAQ_LARGE_CAP_MIN_B * 1e9
        for r in rows:
            sym = str(r.get("symbol", "")).upper().strip().replace(".", "-")
            if not sym:
                continue
            try:
                cap = float(r.get("marketCap") or 0)
            except (TypeError, ValueError):
                continue
            if cap >= min_cap:
                tickers.append(sym)

        tickers = sorted(set(tickers))
        logger.info("Fetched %d NASDAQ large-cap tickers (≥$%.0fB) from NASDAQ screener", len(tickers), _NASDAQ_LARGE_CAP_MIN_B)
        return tickers
    except Exception as exc:
        logger.error("Failed to fetch NASDAQ large-cap tickers: %s", exc, exc_info=True)
        return []
```

- [ ] **Step 4: Update `fetch_universe()` to include the new list**

Replace `fetch_universe()` (~line 279):

```python
def fetch_universe() -> list[str]:
    """Return the deduplicated union of S&P 500, NASDAQ 100, and NASDAQ large-cap tickers."""
    sp500       = fetch_sp500_tickers()
    nasdaq100   = fetch_nasdaq100_tickers()
    nasdaq_large = fetch_nasdaq_large_cap_tickers()
    combined = sorted(set(sp500) | set(nasdaq100) | set(nasdaq_large))
    logger.info(
        "Universe: %d S&P500 + %d NDX100 + %d NASDAQ≥$2B = %d unique",
        len(sp500), len(nasdaq100), len(nasdaq_large), len(combined),
    )
    return combined
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_csp_scanner_integration.py -v
```

Expected: new tests PASS, existing tests unchanged

- [ ] **Step 6: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_integration.py
git commit -m "feat(scanner): add fetch_nasdaq_large_cap_tickers and expand fetch_universe"
```

---

## Task 3: Refresh pipeline — stamp `universes` membership on fundamentals

**Files:**
- Modify: `src/market_data/refresh.py`
- Test: `tests/test_market_data_refresh.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_market_data_refresh.py`:

```python
def test_refresh_universe_stamps_universes_tag(monkeypatch):
    """refresh_universe() should tag each fundamental row with its universe membership."""
    ensure_tables()

    # Stub universe fetchers: AAPL in all three, MSFT in sp500+nasdaq100, AMZN only in nasdaq_large
    monkeypatch.setattr("src.market_data.refresh.fetch_sp500_tickers",     lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr("src.market_data.refresh.fetch_nasdaq100_tickers", lambda: ["AAPL", "MSFT"])
    monkeypatch.setattr("src.market_data.refresh.fetch_nasdaq_large_cap_tickers", lambda: ["AAPL", "AMZN"])

    # Stub OHLCV download to return empty (we only care about fundamentals here)
    monkeypatch.setattr("src.market_data.refresh._download_ohlcv_batch", lambda symbols, period="5d": {})

    # Stub fundamental fetch to return bare rows (no universes yet)
    def _mock_fundamentals(symbols):
        return [{"symbol": s, "market_cap_b": 10.0, "price": 100.0, "beta": 1.0, "iv_pct": None}
                for s in symbols]
    monkeypatch.setattr("src.market_data.refresh._fetch_fundamentals_batch", _mock_fundamentals)

    refresh_universe(full=False)

    all_rows = get_all_fundamentals()
    lookup = {r["symbol"]: r for r in all_rows}

    # AAPL: sp500 + nasdaq100 + nasdaq_large
    assert "nasdaq_large" in lookup["AAPL"]["universes"]
    assert "nasdaq100" in lookup["AAPL"]["universes"]
    assert "sp500" in lookup["AAPL"]["universes"]

    # MSFT: sp500 + nasdaq100 only
    assert "sp500" in lookup["MSFT"]["universes"]
    assert "nasdaq100" in lookup["MSFT"]["universes"]
    assert "nasdaq_large" not in lookup["MSFT"]["universes"]

    # AMZN: nasdaq_large only
    assert lookup["AMZN"]["universes"] == "nasdaq_large"
```

Also update the import line at the top of the test file to add `fetch_nasdaq_large_cap_tickers` from `src.market_data.refresh` if needed — but actually `refresh.py` imports it from `csp_scanner`, so in the test we patch `src.market_data.refresh.fetch_nasdaq_large_cap_tickers`.

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_market_data_refresh.py::test_refresh_universe_stamps_universes_tag -v
```

Expected: FAIL — `AttributeError: module 'src.market_data.refresh' has no attribute 'fetch_nasdaq_large_cap_tickers'`

- [ ] **Step 3: Update `refresh.py` imports**

Find the import line at the top (~line 22):

```python
from ..screener.csp_scanner import fetch_sp500_tickers, fetch_nasdaq100_tickers
```

Replace with:

```python
from ..screener.csp_scanner import fetch_sp500_tickers, fetch_nasdaq100_tickers, fetch_nasdaq_large_cap_tickers
```

- [ ] **Step 4: Update `refresh_universe()` to build membership map and stamp rows**

Replace the universe-fetching block and fundamentals upsert block in `refresh_universe()`.

Replace from `# 1. Fetch universe` through the universe logging (~lines 167–172):

```python
    # 1. Fetch universe and build membership map
    logger.info("Fetching universe constituent lists...")
    sp500        = fetch_sp500_tickers()
    nasdaq100    = fetch_nasdaq100_tickers()
    nasdaq_large = fetch_nasdaq_large_cap_tickers()

    from collections import defaultdict
    membership: dict[str, set[str]] = defaultdict(set)
    for sym in sp500:        membership[sym].add("sp500")
    for sym in nasdaq100:    membership[sym].add("nasdaq100")
    for sym in nasdaq_large: membership[sym].add("nasdaq_large")

    universe = sorted(membership.keys())
    logger.info(
        "Universe: %d S&P500 + %d NDX100 + %d NASDAQ≥$2B = %d unique tickers",
        len(sp500), len(nasdaq100), len(nasdaq_large), len(universe),
    )
```

Then in the fundamentals loop, replace:

```python
        fund_rows = _fetch_fundamentals_batch(batch)
        if fund_rows:
            upserted = bulk_upsert_fundamentals(fund_rows)
```

with:

```python
        fund_rows = _fetch_fundamentals_batch(batch)
        for row in fund_rows:
            row["universes"] = ",".join(sorted(membership.get(row["symbol"], set())))
        if fund_rows:
            upserted = bulk_upsert_fundamentals(fund_rows)
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_market_data_refresh.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/market_data/refresh.py tests/test_market_data_refresh.py
git commit -m "feat(refresh): stamp universe membership tags on fundamental rows"
```

---

## Task 4: Scanner filter — `restrict_to_watchlist_universe` param

**Files:**
- Modify: `src/screener/csp_scanner.py`
- Test: `tests/test_csp_scanner_integration.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_csp_scanner_integration.py`:

```python
from src.screener.csp_scanner import ScannerParams, _fundamental_filter_from_store


def test_restrict_to_watchlist_universe_filters_correctly():
    """When restrict_to_watchlist_universe=True, only sp500/nasdaq100 rows pass."""
    store_lookup = {
        "AAPL": {"symbol": "AAPL", "market_cap_b": 100.0, "price": 150.0, "beta": 1.0,
                 "iv_pct": 30.0, "fcf": 10.0, "forward_pe": 20.0, "universes": "nasdaq100,sp500"},
        "AMZN": {"symbol": "AMZN", "market_cap_b": 50.0, "price": 120.0, "beta": 1.1,
                 "iv_pct": 35.0, "fcf": 8.0, "forward_pe": 25.0, "universes": "nasdaq_large"},
        "NEWC": {"symbol": "NEWC", "market_cap_b": 5.0, "price": 40.0, "beta": 1.2,
                 "iv_pct": 40.0, "fcf": 1.0, "forward_pe": 15.0, "universes": "nasdaq_large"},
    }
    params_restricted = ScannerParams(
        min_market_cap_b=1.0, max_price=500.0, min_beta=0.5, max_beta=3.0,
        min_fcf_b=None, max_debt_to_equity=None, min_revenue_growth=None,
        restrict_to_watchlist_universe=True,
    )
    tickers, rows = _fundamental_filter_from_store(
        list(store_lookup.keys()), params_restricted, store_lookup
    )
    assert "AAPL" in tickers
    assert "AMZN" not in tickers
    assert "NEWC" not in tickers


def test_restrict_to_watchlist_universe_false_passes_all():
    """When restrict_to_watchlist_universe=False (default), all passing tickers pass."""
    store_lookup = {
        "AAPL": {"symbol": "AAPL", "market_cap_b": 100.0, "price": 150.0, "beta": 1.0,
                 "iv_pct": 30.0, "fcf": 10.0, "forward_pe": 20.0, "universes": "nasdaq100,sp500"},
        "AMZN": {"symbol": "AMZN", "market_cap_b": 50.0, "price": 120.0, "beta": 1.1,
                 "iv_pct": 35.0, "fcf": 8.0, "forward_pe": 25.0, "universes": "nasdaq_large"},
    }
    params_open = ScannerParams(
        min_market_cap_b=1.0, max_price=500.0, min_beta=0.5, max_beta=3.0,
        min_fcf_b=None, max_debt_to_equity=None, min_revenue_growth=None,
        restrict_to_watchlist_universe=False,
    )
    tickers, rows = _fundamental_filter_from_store(
        list(store_lookup.keys()), params_open, store_lookup
    )
    assert "AAPL" in tickers
    assert "AMZN" in tickers


def test_scanner_params_cache_key_differs_by_universe_flag():
    p1 = ScannerParams(restrict_to_watchlist_universe=False)
    p2 = ScannerParams(restrict_to_watchlist_universe=True)
    assert p1.cache_key_suffix() != p2.cache_key_suffix()
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/test_csp_scanner_integration.py::test_restrict_to_watchlist_universe_filters_correctly tests/test_csp_scanner_integration.py::test_restrict_to_watchlist_universe_false_passes_all tests/test_csp_scanner_integration.py::test_scanner_params_cache_key_differs_by_universe_flag -v
```

Expected: FAIL — `ScannerParams.__init__() got an unexpected keyword argument 'restrict_to_watchlist_universe'`

- [ ] **Step 3: Add `restrict_to_watchlist_universe` to `ScannerParams`**

In `ScannerParams` (~line 135), add the new field after `conditions`:

```python
    conditions: list[str] = field(default_factory=list)
    restrict_to_watchlist_universe: bool = False
```

- [ ] **Step 4: Add `restrict_to_watchlist_universe` to `ScannerParams.from_query()`**

In `from_query()` (~line 164), add the parameter:

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
        restrict_to_watchlist_universe: bool = False,
    ) -> "ScannerParams":
```

And in the `return cls(...)` block, add:

```python
            restrict_to_watchlist_universe = restrict_to_watchlist_universe,
```

- [ ] **Step 5: Add filter logic in `_fundamental_filter_from_store()`**

In `_fundamental_filter_from_store()` (~line 339), add the universe membership check at the top of the per-ticker loop, right before the `market_cap_b` check:

```python
        # Universe membership gate (when restricted to watchlist universe)
        if params.restrict_to_watchlist_universe:
            universes = row.get("universes") or ""
            if "sp500" not in universes and "nasdaq100" not in universes:
                continue
```

- [ ] **Step 6: Run tests**

```bash
.venv/bin/pytest tests/test_csp_scanner_integration.py -v
```

Expected: all PASS

- [ ] **Step 7: Commit**

```bash
git add src/screener/csp_scanner.py tests/test_csp_scanner_integration.py
git commit -m "feat(scanner): add restrict_to_watchlist_universe param to ScannerParams"
```

---

## Task 5: API — wire up `restrict_to_watchlist_universe` query param

**Files:**
- Modify: `src/api/main.py`

No new tests needed — this is a thin wiring layer and is covered by the scanner integration tests.

- [ ] **Step 1: Add param to `GET /api/screener/csp-scan`**

In `get_csp_scan_candidates()` (~line 305), add the new query param:

```python
async def get_csp_scan_candidates(
    min_cap:    float | None = None,
    max_price:  float | None = None,
    min_beta:   float | None = None,
    max_beta:   float | None = None,
    min_vol:    float | None = None,
    max_rsi:    float | None = None,
    min_adx:    float | None = None,
    max_adx:    float | None = None,
    min_dte:    int   | None = None,
    max_dte:    int   | None = None,
    conditions: str   | None = None,
    min_fcf_b:           float | None = Query(default=0.0),
    max_debt_to_equity:  float | None = Query(default=2.0),
    min_revenue_growth:  float | None = Query(default=-0.10),
    min_earnings_growth: float | None = Query(default=None),
    min_dividend_yield:  float | None = Query(default=None),
    restrict_to_watchlist_universe: bool = False,
):
```

And in the `ScannerParams.from_query(...)` call (~line 345), add:

```python
        restrict_to_watchlist_universe=restrict_to_watchlist_universe,
```

- [ ] **Step 2: Add same param to `DELETE /api/screener/csp-scan`**

Find `invalidate_csp_scan_cache()` (~line 392). It mirrors `get_csp_scan_candidates` signature. Add the same param and pass it to `ScannerParams.from_query()`.

- [ ] **Step 3: Run the full test suite**

```bash
.venv/bin/pytest --ignore=tests/test_stock_screener.py -v
```

Expected: all PASS

- [ ] **Step 4: Commit**

```bash
git add src/api/main.py
git commit -m "feat(api): add restrict_to_watchlist_universe param to csp-scan endpoint"
```

---

## Task 6: UI — universe toggle button

**Files:**
- Modify: `src/web/scanner.js`
- Modify: `src/web/scanner.html`

- [ ] **Step 1: Add `restrict_to_watchlist_universe` to `_state.params` in `scanner.js`**

In the `_state` object (~line 38), add the new field after `min_dividend_yield`:

```js
        min_dividend_yield:  null,
        restrict_to_watchlist_universe: false,
```

- [ ] **Step 2: Add restore logic in `_restoreParams()`**

In `_restoreParams()` (~line 95), add after the conditions restore:

```js
        if (typeof saved.restrict_to_watchlist_universe === 'boolean') p.restrict_to_watchlist_universe = saved.restrict_to_watchlist_universe;
```

- [ ] **Step 3: Add `restrict_to_watchlist_universe` to `_buildQueryString()`**

In `_buildQueryString()` (~line 374), add before `return qs.toString()`:

```js
    if (_state.params.restrict_to_watchlist_universe) qs.set('restrict_to_watchlist_universe', 'true');
```

- [ ] **Step 4: Add toggle function to `scanner.js`**

Add after `disableNullableBadge()` (~line 200):

```js
function toggleWatchlistUniverse() {
    _state.params.restrict_to_watchlist_universe = !_state.params.restrict_to_watchlist_universe;
    _persistParams();
    const btn = document.getElementById('btn-watchlist-universe');
    if (btn) btn.classList.toggle('active', _state.params.restrict_to_watchlist_universe);
}
```

- [ ] **Step 5: Initialize toggle button state on page load**

In the `DOMContentLoaded` handler (~line 135), add after `renderParamBadges()`:

```js
    const univBtn = document.getElementById('btn-watchlist-universe');
    if (univBtn) univBtn.classList.toggle('active', _state.params.restrict_to_watchlist_universe);
```

- [ ] **Step 6: Update scan status message to reflect current universe**

In `startScan()` (~line 383), replace the status message:

```js
    const universeLabel = _state.params.restrict_to_watchlist_universe
        ? 'S&P 500 + NASDAQ 100 universe'
        : 'Full NASDAQ universe (≥$2B)';
    setStatus('running', `<span class="spinner"></span> Scanning ${universeLabel}… This may take 3–6 minutes on a cold cache.`);
```

- [ ] **Step 7: Add toggle button to `scanner.html`**

Between the `conditions-area` div and the `scan-controls` div (~line 354), add:

```html
            <!-- Universe scope toggle -->
            <div class="universe-toggle-area">
                <button id="btn-watchlist-universe" class="btn-universe-toggle" onclick="toggleWatchlistUniverse()">
                    S&P 500 / NASDAQ 100 only
                </button>
            </div>
```

- [ ] **Step 8: Add CSS for the toggle button to `scanner.html` inline styles**

In the `<style>` block at the top of `scanner.html`, add:

```css
        .btn-universe-toggle {
            padding: 5px 12px;
            border-radius: 14px;
            border: 1px solid var(--border-color, #333);
            background: transparent;
            color: var(--text-muted, #888);
            font-size: 0.8rem;
            cursor: pointer;
            transition: background 0.15s, color 0.15s, border-color 0.15s;
        }
        .btn-universe-toggle:hover {
            border-color: var(--text-secondary, #aaa);
            color: var(--text-primary, #fff);
        }
        .btn-universe-toggle.active {
            background: var(--accent, #4f6ef7);
            border-color: var(--accent, #4f6ef7);
            color: #fff;
        }
        .universe-toggle-area {
            margin: 8px 0 4px;
        }
```

- [ ] **Step 9: Commit**

```bash
git add src/web/scanner.js src/web/scanner.html
git commit -m "feat(ui): add S&P 500 / NASDAQ 100 universe toggle to CSP scanner"
```

---

## Verification

### Automated

```bash
# Full test suite (excluding pre-existing broken test)
.venv/bin/pytest --ignore=tests/test_stock_screener.py -v
```

All tests should PASS.

### End-to-end (Docker)

1. **Run incremental refresh** — picks up the new universe:
   ```bash
   docker compose run --rm market-data-refresh
   ```
   Check logs: should report `S&P500 + NDX100 + NASDAQ≥$2B = N unique tickers` with N > 600.

2. **Verify DB tagging:**
   ```bash
   docker compose run --rm api python3 -c "
   from src.market_data.store import get_all_fundamentals
   rows = get_all_fundamentals()
   tagged = [r for r in rows if r['universes']]
   print(f'{len(rows)} rows, {len(tagged)} with universes tag')
   from collections import Counter
   print(Counter(tag for r in rows for tag in r['universes'].split(',') if tag))
   "
   ```
   Expected: `sp500`, `nasdaq100`, `nasdaq_large` all appear with realistic counts.

3. **API smoke test:**
   ```bash
   # Full universe
   curl "http://localhost:8000/api/screener/csp-scan?min_cap=2" | jq '.filter_summary'
   # Watchlist only
   curl "http://localhost:8000/api/screener/csp-scan?min_cap=2&restrict_to_watchlist_universe=true" | jq '.filter_summary'
   ```
   Second call should show fewer `combined_unique` entries.

4. **UI smoke test:** Open the scanner page, confirm the "S&P 500 / NASDAQ 100 only" button appears, toggles active state, and triggers a new scan with different result counts.
