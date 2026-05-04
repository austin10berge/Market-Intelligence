# Balance Sheet & Profitability Filters — Design Spec

**Date:** 2026-05-03
**Branch:** feature/csp-universe-scanner

---

## Context

The CSP universe scanner's fundamental filter currently gates on three metrics: market cap, price, and beta. This is enough to screen for size and volatility profile, but says nothing about the quality of the underlying business.

The core CSP trading philosophy is drawdown survival: would you hold this stock through a 30–50% decline? That question is answered by balance sheet strength and earnings quality, not beta alone. A company with positive FCF, manageable debt, and stable revenue can weather a bad year; one with negative FCF and a leveraged balance sheet may not recover.

This spec adds five new hard-gate filters to the fundamental stage, backed by pre-stored data in `universe_fundamentals`.

---

## Metrics

All five metrics come from `yfinance` `ticker.info` — the same call already used to fetch market cap, price, beta, and IV. No new API dependencies.

| Filter param | yfinance field | What it measures | Default |
|---|---|---|---|
| `min_fcf` | `freeCashflow` | TTM free cash flow in billions (stored as raw $, divided by 1e9 for display/input) | `0.0` (FCF must be positive) |
| `max_debt_to_equity` | `debtToEquity` | Total debt / shareholders equity | `2.0` |
| `min_revenue_growth` | `revenueGrowth` | YoY revenue growth (decimal, e.g. `0.12` = 12%) | `-0.10` (≥ −10%) |
| `min_earnings_growth` | `earningsGrowth` | YoY net income growth (decimal) | `None` (gate off) |
| `min_dividend_yield` | `dividendYield` | Annual dividend yield (decimal, e.g. `0.03` = 3%) | `None` (gate off) |

**Missing data policy:** If yfinance returns `None` for a field, the ticker passes that gate. Missing data ≠ disqualified. This avoids incorrectly eliminating tickers with sparse reporting (ADRs, newer index entrants).

---

## Data Layer

### `universe_fundamentals` schema — new columns

Five columns added to the `CREATE TABLE IF NOT EXISTS` statement in `src/market_data/store.py`. For the already-deployed table, use `ALTER TABLE ... ADD COLUMN` wrapped in a `try/except` (SQLite has no `ADD COLUMN IF NOT EXISTS`):

```python
for col in ["fcf REAL", "debt_to_equity REAL", "revenue_growth REAL",
            "earnings_growth REAL", "dividend_yield REAL"]:
    try:
        conn.execute(f"ALTER TABLE universe_fundamentals ADD COLUMN {col}")
    except sqlite3.OperationalError:
        pass  # column already exists
```

```sql
fcf              REAL,   -- freeCashflow / 1e9, TTM billions (matches market_cap_b convention)
debt_to_equity   REAL,   -- debtToEquity ratio
revenue_growth   REAL,   -- revenueGrowth decimal
earnings_growth  REAL,   -- earningsGrowth decimal
dividend_yield   REAL    -- dividendYield decimal
```

### `refresh.py` — `_fetch_fundamentals_batch`

In `src/market_data/refresh.py`, extend the per-ticker `info` extraction to pull the five new fields alongside the existing `marketCap`, `currentPrice`, `beta`, `impliedVolatility`:

```python
fcf             = info.get("freeCashflow")           # may be None
debt_to_equity  = info.get("debtToEquity")           # may be None
revenue_growth  = info.get("revenueGrowth")          # may be None
earnings_growth = info.get("earningsGrowth")         # may be None
dividend_yield  = info.get("dividendYield")          # may be None
```

Write all five into the upsert. FCF is divided by `1e9` before storage (same treatment as `market_cap_b`) so the DB value and UI input are in billions. The gate comparison in the scanner uses the same billions unit. All other fields stored as-is (ratios and decimals).

---

## Scanner Layer

### `ScannerParams` — new fields (`src/screener/csp_scanner.py`)

```python
min_fcf:             float | None = 0.0      # None = gate off
max_debt_to_equity:  float | None = 2.0      # None = gate off
min_revenue_growth:  float | None = -0.10    # None = gate off
min_earnings_growth: float | None = None     # off by default
min_dividend_yield:  float | None = None     # off by default
```

`from_query()` maps five new API query params to these fields. `cache_key_suffix()` already MD5-hashes all fields — no change needed there.

### Fundamental filter — new gate logic

Added at the end of `apply_fundamental_filter` (or inline in `run_csp_scan` Stage 1), after existing market cap / price / beta checks:

```python
if params.min_fcf is not None and row.fcf is not None:
    if row.fcf < params.min_fcf:
        filtered_out(); continue

if params.max_debt_to_equity is not None and row.debt_to_equity is not None:
    if row.debt_to_equity > params.max_debt_to_equity:
        filtered_out(); continue

if params.min_revenue_growth is not None and row.revenue_growth is not None:
    if row.revenue_growth < params.min_revenue_growth:
        filtered_out(); continue

if params.min_earnings_growth is not None and row.earnings_growth is not None:
    if row.earnings_growth < params.min_earnings_growth:
        filtered_out(); continue

if params.min_dividend_yield is not None and row.dividend_yield is not None:
    if row.dividend_yield < params.min_dividend_yield:
        filtered_out(); continue
```

The existing filter summary funnel tracks the fundamental stage count — new gates reduce that count automatically with no additional instrumentation.

---

## API Layer (`src/api/main.py`)

Five new optional query params on `GET /api/screener/csp-scan`:

| Query param | Type | Default |
|---|---|---|
| `min_fcf` | `float \| None` | `0.0` |
| `max_debt_to_equity` | `float \| None` | `2.0` |
| `min_revenue_growth` | `float \| None` | `-0.10` |
| `min_earnings_growth` | `float \| None` | `None` |
| `min_dividend_yield` | `float \| None` | `None` |

Passing `null` or omitting the param = gate disabled.

---

## Frontend (`src/web/`)

### Filter badges (`scanner.html` / `scanner.js`)

Five new editable badges in the fundamental filter row, after the existing Cap / Price / Beta badges:

```
Cap > 10B  |  Price < $150  |  Beta 0.8–2.4  |  FCF > $0B  |  D/E < 2.0  |  Rev > -10%
```

FCF badge input is in billions (same as Cap), so "0" means $0B and "5" means $5B FCF.

Disabled-by-default filters (`min_earnings_growth`, `min_dividend_yield`) render as greyed-out badges. Clicking activates them and sets a starting value.

Same interaction pattern as existing badges:
- Click → enter edit mode with constrained input
- Blur / Enter → commit
- Escape → cancel
- State persisted to localStorage under the existing `market-intelligence:csp-scanner-params` key

### State object additions (`scanner.js`)

```js
min_fcf: 0,                 // maps to API param min_fcf
max_debt_to_equity: 2.0,    // maps to API param max_debt_to_equity
min_revenue_growth: -0.10,  // maps to API param min_revenue_growth
min_earnings_growth: null,  // null = disabled
min_dividend_yield: null,   // null = disabled
```

`_buildQueryString()` omits `null` params entirely (same as existing pattern for optional params).

---

## Verification

1. **Data refresh:** `docker compose run --rm market-data-refresh` → spot-check new columns:
   ```sql
   SELECT symbol, fcf, debt_to_equity, revenue_growth FROM universe_fundamentals LIMIT 10;
   ```
   Expect non-null values for major S&P 500 names (AAPL, MSFT, JPM, etc.).

2. **Gate tightening:** `GET /api/screener/csp-scan?max_debt_to_equity=0.1` → fundamental pass count drops vs. default run.

3. **Absurd gate:** `GET /api/screener/csp-scan?min_fcf=9999999999999` → 0 fundamental survivors.

4. **Regression (gates off):** `GET /api/screener/csp-scan?min_fcf=null&max_debt_to_equity=null&min_revenue_growth=null` → identical results to pre-feature baseline.

5. **Existing tests:** `.venv/bin/pytest tests/test_csp_scanner_conditions.py -v` must pass unchanged.
