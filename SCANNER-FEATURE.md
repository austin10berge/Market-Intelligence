# CSP Scanner Feature — Development Context

> Living document. Update when making structural changes to this feature.
> Branch: `feature/csp-scanner`
> Started: 2026-04-29

---

## Goal

Identify new Cash-Secured Put (CSP) candidates from a broad, systematically-defined
universe — S&P 500 + NASDAQ 100 — rather than a manually curated watchlist.
This is distinct from the existing CSP screener, which runs against `app_config.watchlist`
(a user-managed ticker list). The scanner runs against the full index universe and pre-filters
it by fundamental + volatility criteria before handing qualifying tickers to the existing
options screening machinery.

---

## Screening Criteria

| Filter | Value |
|---|---|
| Universe | S&P 500 ∪ NASDAQ 100 (U.S. optionable stocks only) |
| Market cap | > $10B |
| Price | < $150 |
| Beta | 0.8 – 2.4 |
| IV / Vol filter | ≥ 30% — IV primary (from `Ticker.info`), RV-20 fallback if IV is None. Both always computed for IV/RV ratio. |

Index membership is fetched live from Wikipedia (no API key required). Stocks appearing
in both indices are deduplicated. ETFs from the indices (SPY, QQQ, etc.) are excluded
because they are not individual optionable stocks with fundamentals.

---

## Architecture

### New files

| File | Purpose |
|---|---|
| `src/screener/csp_scanner.py` | Universe fetch, fundamental pre-filter, RV calculation, feeds into `screen_csp_candidates()` |

### Modified files

| File | Change |
|---|---|
| `src/api/main.py` | New endpoint `GET /api/screener/csp-scan` |
| `src/cache.py` | New cache key constant `KEY_SCREENER_CSP_SCAN` |
| `src/web/scanner.html` | New dashboard page |
| `src/web/scanner.js` | JS for scanner page |
| `src/web/nginx.conf` | Route `/scanner` to `scanner.html` |

### Does NOT modify

- `src/screener/options.py` — no changes, `screen_csp_candidates()` is called unchanged
- `src/db.py` — no new tables; scanner results are not persisted (ephemeral per-request)
- `src/screener/stocks.py` — no changes
- Any existing watchlist/settings behavior

---

## Data Flow

```
GET /api/screener/csp-scan
  │
  ├─ cache hit?  → return cached result
  │
  └─ cache miss
       │
       ├─ fetch_sp500_tickers()     → Wikipedia S&P 500 table
       ├─ fetch_nasdaq100_tickers() → Wikipedia NASDAQ 100 table
       │   → deduplicate, ~600 unique tickers
       │
       ├─ apply_fundamental_filter() — yfinance Ticker.info, batched 50/req
       │   • quoteType == EQUITY
       │   • market_cap > 10B
       │   • price < 150
       │   • 0.8 ≤ beta ≤ 2.4
       │   • IV (impliedVolatility) captured here at no extra cost
       │   → typically 50–150 tickers pass
       │
       ├─ apply_vol_filter() — IV primary gate, RV-20 fallback
       │   • IV ≥ 30%          → PASS  (vol_gate = "iv")
       │   • IV is None AND rv20 ≥ 30% → PASS  (vol_gate = "rv_fallback")
       │   • IV < 30%          → FAIL
       │   • RV-20 always computed for IV/RV ratio display
       │   → typically 20–60 tickers pass
       │
       └─ screen_csp_candidates(qualifying_tickers)
            → same RSI/ADX/Alpaca/scoring pipeline as the watchlist screener
            → results cached with screener_ttl()
```

---

## Cache Key

`screener:csp-scan` — same TTL strategy as other screeners (5 min market open, up to
4h market closed). Invalidated by `invalidate_screener_cache()` (pattern `screener:*`).

---

## API Response

`GET /api/screener/csp-scan` returns:

```json
{
  "candidates": [ ... ],       // same schema as /api/screener/csp
  "universe_size": 612,        // tickers in combined index universe
  "fundamental_passed": 87,    // passed market cap + price + beta filter
  "rv_passed": 34,             // passed RV ≥ 30% filter
  "filter_summary": {
    "sp500_count": 503,
    "nasdaq100_count": 101,
    "combined_unique": 612,
    "fundamental_passed": 87,
    "rv_passed": 34,
    "options_screener_returned": 12
  },
  "cached": false,
  "cached_at": null,
  "market_status": "Market Closed"
}
```

---

## Known Constraints / Decisions

- **Wikipedia scraping**: S&P 500 and NASDAQ 100 constituent lists are scraped from
  Wikipedia using `pandas.read_html()`. This is reliable (Wikipedia is updated promptly
  after index changes) and requires no API key. The alternative (paying for a data
  vendor index feed) is out of scope.

- **No persistence**: Scanner results are not written to SQLite. They are ephemeral,
  refreshed on demand, cached in Redis. Rationale: the universe changes slowly, and
  options prices are stale after a few minutes anyway.

- **Separate from watchlist screener**: The scanner does not modify `app_config.watchlist`.
  The two screeners are independent. A user might want to run the scanner to discover
  new candidates, then manually add them to the watchlist.

- **Rate limit awareness**: The fundamental pre-filter runs yfinance `Ticker.info` calls
  sequentially (not batched) for the ~600-ticker universe. yfinance v0.2+ provides
  `download()` for price data and `fast_info` for prices, but `info` (for beta, market
  cap) still requires individual calls. To stay within yfinance's informal rate limits,
  we batch in groups of 50 with a 1-second sleep between batches.

- **ETF exclusion**: Index ETFs (SPY, QQQ, IWM, DIA, GLD, TLT, etc.) are not individual
  stocks and their "beta" and "market cap" fields from yfinance are unreliable for this
  purpose. We exclude tickers where `info.get('quoteType') != 'EQUITY'`.

---

## UI (scanner.html / scanner.js)

- Matches the existing dashboard visual language (glass cards, Inter font, same CSS)
- Nav link added to index.html header
- Displays: filter funnel summary at top, then CSP candidates table (same columns as
  existing CSP section but with source universe context)
- "Run Scan" button triggers `GET /api/screener/csp-scan`; results are not auto-loaded
  on page open (scan is expensive, ~2–5 min for full universe)
- Shows progress/status while scan is running (polls every 5s)

---

## Testing

Manual test steps:
1. `docker compose exec api python -c "from src.screener.csp_scanner import fetch_universe; print(len(fetch_universe()))"`
   → Should print ~580–620
2. `curl http://localhost:8000/api/screener/csp-scan`
   → Should return JSON with filter_summary and candidates list
3. Open `https://market.austin10berge.com/scanner`
   → Scanner page loads, Run Scan button works

---

## Status

- [x] `SCANNER-FEATURE.md` created
- [x] `src/screener/csp_scanner.py` implemented
- [x] `src/api/main.py` endpoint added
- [x] `src/cache.py` key constant added
- [x] `src/web/scanner.html` created
- [x] `src/web/scanner.js` created
- [x] `src/web/nginx.conf` updated
- [ ] End-to-end test on Docker stack
