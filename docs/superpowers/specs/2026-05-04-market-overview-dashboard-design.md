# Market Overview Dashboard — Design Spec

**Date:** 2026-05-04
**Branch:** feature/market-overview-dashboard
**Status:** Approved

## Summary

Replace the "Latest Market Signals" text-card section on the main dashboard with a rich, live Market Overview panel. The panel displays sector performance (1D + 1W %), VIX with percentage changes and term structure, GEX with rolling average context and improved bucketing, and market breadth (% above 200d MA + A/D ratio). The LLM synthesis box is preserved as its own standalone card below the panel.

---

## Architecture & Data Flow

A new module `src/fetchers/market_overview.py` contains a standalone async `fetch_market_overview()` function (not a `BaseFetcher` subclass — it is not part of the nightly scoring pipeline). It collects all four signals in one call and returns a structured dict.

```
Browser → GET /api/market-overview
           → Redis cache hit? → return
           → cache miss → fetch_market_overview()
                            ├── yfinance sector ETFs (period="10d") → 1D + 1W %
                            ├── yfinance ^VIX + ^VIX3M (period="10d") → 1D + 1W % + term structure
                            ├── SqueezeMetrics DIX.csv → GEX value + 20-day rolling avg + bucket label
                            └── universe_daily_ohlcv (SQLite) → % above 200d MA + today's A/D ratio
           → cache set (5-min TTL market hours, persists until next open outside hours)
           → return JSON
```

Breadth reads from the local `universe_daily_ohlcv` table — no external HTTP call needed.

---

## API

### `GET /api/market-overview`

Cache key: `market_overview`
TTL: 5 minutes during market hours, 23 hours outside (same logic as screener TTL).

**Response shape:**
```json
{
  "sectors": {
    "XLK": { "name": "Technology", "pct_1d": 1.2, "pct_1w": -0.8, "pct_1m": 3.4 },
    "XLF": { "name": "Financials", "pct_1d": 0.4, "pct_1w": 1.1, "pct_1m": -1.2 }
  },
  "rotation": "Risk-on (cyclical leading)",
  "vix": {
    "spot": 18.4,
    "pct_1d": -3.2,
    "pct_1w": 12.1,
    "vix3m": 19.1,
    "term_structure": "Contango",
    "spread": 0.7,
    "stress_note": "normal, calm"
  },
  "gex": {
    "value_b": 7.4,
    "rolling_20d_avg_b": 5.1,
    "trend": "Rising",
    "label": "High Positive — Strong pinning",
    "bucket": "high"
  },
  "breadth": {
    "pct_above_200ma": 61.2,
    "advancing": 312,
    "declining": 188,
    "ad_ratio": 1.66
  },
  "cached": true,
  "cached_at": "2026-05-04T14:30:00Z",
  "market_status": "open"
}
```

---

## Backend Logic

### Sector ETFs (`src/fetchers/market_overview.py`)

- Tickers: all 11 SPDR sector ETFs (XLK, XLF, XLE, XLV, XLI, XLB, XLU, XLP, XLY, XLRE, XLC)
- Download `period="30d"` to guarantee at least 22 trading days of data
- 1D %: `(close[-1] - close[-2]) / close[-2] * 100`
- 1W %: `(close[-1] - close[-6]) / close[-6] * 100` (5 trading days back); null if fewer than 6 rows
- 1M %: `(close[-1] - close[-22]) / close[-22] * 100` (22 trading days back); null if fewer than 22 rows
- Rotation label reused from existing `sector_etf.py` logic (defensive vs cyclical avg)

### VIX (`src/fetchers/market_overview.py`)

- Tickers: `^VIX`, `^VIX3M`
- Download `period="10d"`
- 1D % and 1W % computed same as sectors
- Term structure: same thresholds as existing `vix.py` (spread > 0.5 = Contango, < -0.5 = Backwardation, else Flat)

### GEX (`src/fetchers/market_overview.py`)

- Source: SqueezeMetrics `DIX.csv` (same URL as existing `gex.py`)
- Parse all rows; take last 20 data points to compute `rolling_20d_avg_b`
- Current value: last row GEX / 1e9
- Trend: "Rising" if current > avg * 1.1, "Falling" if current < avg * 0.9, else "Flat"
- Bucket labels:
  - `< 0B` → "Negative — High volatility risk"
  - `0–3B` → "Low Positive — Muted hedging"
  - `3–7B` → "Moderate Positive — Normal pinning"
  - `7–12B` → "High Positive — Strong pinning"
  - `> 12B` → "Extreme — Max pinning, expect low realized vol"

### Breadth (`src/fetchers/market_overview.py`)

- Source: `universe_daily_ohlcv` SQLite table (already populated by the market data refresh job)
- % above 200d MA: for each ticker with ≥ 200 rows of history, check if `close[-1] > mean(close[-200:])`. Count and divide.
- A/D ratio: for the most recent date in the store, count tickers where today's `close > yesterday's close` (advancing) vs `close < yesterday's close` (declining). Return both counts and ratio.
- If the store has < 50 tickers with sufficient history, return `null` for breadth and log a warning.

---

## Frontend

### `src/web/index.html`

- The existing `<section class="glass card full-width">` containing `id="signals-list"` and the `.llm-box` is replaced with two separate sections:
  1. `<section id="market-overview-section">` — the new 4-panel grid
  2. `<section id="llm-section">` — just the AI Synthesis box

### `src/web/app.js`

- Remove `renderSignals()` function
- Add `fetchMarketOverview()` called from `initDashboard()` alongside existing parallel fetches
- Add rendering functions: `renderSectors()`, `renderVix()`, `renderGex()`, `renderBreadth()`

### Layout

4-panel CSS grid (2 columns on wide screens, 1 column on mobile, breakpoint at 768px):

```
┌─────────────────────┬──────────────┐
│  Sector Performance │     VIX      │
│  (bar chart, 11     │  spot, 1D%,  │
│   sectors ranked)   │  1W%, term   │
├─────────────────────┼──────────────┤
│        GEX          │   Breadth    │
│  value, trend,      │  200d MA %   │
│  bucket label       │  A/D ratio   │
└─────────────────────┴──────────────┘
```

### Sector Bar Chart (pure CSS, no library)

Each sector row:
```
Technology  [████████░░] +1.2%  [░░░████░░░] -0.8%  [░░░░░░████] +3.4%
               1D                   1W                    1M
```
- Container is fixed-width; bars are `<div>` with `width: calc(50% + X%)` style
- Scale: ±5% maps to 0–100% bar fill. Values beyond ±5% clip to full bar.
- 1D bar: solid fill (`--accent-green` / `--accent-red`)
- 1W bar: same color at 55% opacity
- 1M bar: same color at 30% opacity
- Sorted by 1D % descending on render

### VIX Card

```
VIX  18.40
     1D: ↓ -3.2%   1W: ↑ +12.1%
     Contango — normal, calm  (spread +0.70)
```

### GEX Card

```
GEX  $7.4B   High Positive — Strong pinning
     20d avg: $5.1B   ↑ Rising
```

### Breadth Card

```
200d MA   61%  [████████████░░░░░░░░]  (green ≥60%, yellow 40-60%, red <40%)
A/D       312 ↑ / 188 ↓   ratio 1.66  (green ≥1.2, yellow 0.8-1.2, red <0.8)
```

### `src/web/index.css`

- Add `.market-overview-grid` — 2-col CSS grid with gap
- Add `.overview-panel` — glass card style matching existing `.card`
- Add `.sector-bar-row`, `.sector-bar`, `.bar-1d`, `.bar-1w` for the bar chart
- Add `.breadth-fill-bar` for the breadth progress bar
- Mobile: grid collapses to 1 column at 768px

---

## What is Removed

- `renderSignals()` in `app.js` — replaced by four focused render functions
- The `id="signals-list"` grid in `index.html` — replaced by the market overview grid
- The `.llm-box` inside the signals section — moved to its own `<section id="llm-section">`

The nightly pipeline's `daily_signals` table and scoring system are **not changed**. The existing GEX and VIX fetchers (`src/fetchers/gex.py`, `src/fetchers/vix.py`) are **not modified** — they continue running in the pipeline for scoring purposes. The new `market_overview.py` is additive.

---

## Caching

- Redis key: `market_overview`
- TTL: `screener_ttl()` (5 min market hours, ~4h weekends, persists until next open otherwise)
- Cache invalidation: not explicitly invalidated — TTL expiry is sufficient for this use case
- If Redis is unavailable: falls through to live computation (same pattern as all other endpoints)

---

## Testing

- Unit test `fetch_market_overview()` with mocked yfinance and SqueezeMetrics responses (using `respx`)
- Test GEX bucket boundary conditions (0, 3B, 7B, 12B edges)
- Test breadth computation against a small in-memory SQLite fixture
- No new integration tests needed (the endpoint follows the same pattern as existing screener endpoints)
