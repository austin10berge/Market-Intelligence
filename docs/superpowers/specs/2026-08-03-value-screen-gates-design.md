# Value Screen Gates: Gross Margin & Interest Coverage

## Background

A [Reddit post](https://www.reddit.com/r/ValueInvesting/comments/1vdtgks/) (r/ValueInvesting) describes a 4-gate "one strike and you're out" checklist used to build a long-term stock watchlist:

1. **Solvency** — interest coverage ratio > 4.0x
2. **Cash generation** — gross profit margin > 40%
3. **Top-line momentum** — revenue growth YoY > 10%
4. **Valuation** — forward PEG ratio < 1.5x

A stock must pass all 4 to qualify. The OP evaluated tickers manually via an AI chat, one at a time — there's no real screener behind it.

Checking the scanner in `market.austin10berge.com` (this repo): gates 3 and 4 already exist as live filters (`min_revenue_growth`, `max_peg_ratio` in `src/screener/csp_scanner.py`). Gates 1 and 2 do not exist yet. This spec covers adding them.

## Goal

Add `gross_margin` and `interest_coverage` as two new optional numeric filters on the existing scanner, plus a one-click preset that sets all 4 thresholds to the post's exact values. Nothing else from the post (the 14-day "sandbox" cooling-off workflow, the AI qualitative reasoning layer) is in scope.

## Data Layer

`src/market_data/store.py` — add two nullable columns to `universe_fundamentals`:

```sql
gross_margin      REAL,
interest_coverage REAL,
```

Via the existing `_NEW_FUNDAMENTAL_COLUMNS` additive-migration list (safe `ALTER TABLE`, no backfill required — `ensure_tables()` already no-ops when the column exists).

## Fetch Layer

`src/market_data/refresh.py`, in the per-ticker fundamentals loop:

- **`gross_margin`** ← `info.get("grossMargins")`. Same lightweight `yf.Ticker(symbol).info` call already used for `debtToEquity`, `revenueGrowth`, `freeCashflow`, etc. Zero additional API cost. Confirmed live against AAPL: `grossMargins = 0.48653`.

- **`interest_coverage`** ← new call: `yf.Ticker(symbol).get_income_stmt(freq='yearly')`, read the `EBIT` and `InterestExpense` rows (most recent column), compute `EBIT / InterestExpense`. Confirmed live against `T`: `EBIT = 33.81B`, `InterestExpense = 6.80B` → coverage ≈ 4.97x.
  - This is a **second network call per ticker**, separate from and heavier than the batched `.info` fetch. It must go through the same rate-limit guardrails the rest of the pipeline already uses (the fetcher lock / circuit-breaker patterns from the 2026-07-30 perf fixes) rather than firing unthrottled across the ~500-600 ticker universe.
  - Debt-free or near-debt-free companies (e.g. AAPL) report no `InterestExpense` line at all — `get_income_stmt()` returns `None`/`NaN` for that row. When `InterestExpense` is `None` or `0`, store `interest_coverage` as `None`. Do **not** invent an "infinite coverage" sentinel value.

## Filter Layer

`src/screener/csp_scanner.py`:

- Add to `ScannerParams`: `min_gross_margin: float | None = None`, `min_interest_coverage: float | None = None`. Thread both through `from_query(...)`.
- Add threshold checks in `apply_fundamental_filter` / `_fundamental_filter_from_store`, mirroring the existing `revenue_growth`/`peg_ratio` blocks:

```python
gross_margin = row.get("gross_margin")
if params.min_gross_margin is not None and gross_margin is not None:
    if gross_margin < params.min_gross_margin:
        continue  # fail

interest_coverage = row.get("interest_coverage")
if params.min_interest_coverage is not None and interest_coverage is not None:
    if interest_coverage < params.min_interest_coverage:
        continue  # fail
```

- **Missing-data convention (already established in this codebase):** if the stored value is `None`, the gate is skipped for that ticker — same behavior as every other optional filter here today. This is *not* special-cased logic for debt-free companies; it's the pre-existing "missing data doesn't filter" rule, which happens to produce the correct outcome (debt-free → passes the solvency gate) as a side effect. Worth a code comment so it doesn't read as an oversight.

## API Layer

`src/api/main.py` — two new `Query(default=None)` params (`min_gross_margin`, `min_interest_coverage`) on both scanner endpoints, wired into `ScannerParams.from_query(...)`, following the exact pattern of `max_peg_ratio`.

## Frontend

`src/web/scanner.js`, `src/web/scanner.html`, `src/web/v2/scanner.js` — both the legacy and v2 UIs get updated, matching how every existing filter (e.g. `min_revenue_growth`, `max_peg_ratio`) is already duplicated across both:

- `min_gross_margin`: nullable numeric input, scaled ×100 for display (stored as fraction, e.g. `0.40`), same convention as `min_revenue_growth`.
- `min_interest_coverage`: nullable numeric input, plain ratio, no scaling (e.g. `4.0`).
- A **"Reddit value screen" preset button** that sets all 4 params in one click and reruns the scan:
  ```js
  { min_interest_coverage: 4.0, min_gross_margin: 0.40, min_revenue_growth: 0.10, max_peg_ratio: 1.5 }
  ```

## Testing

- `tests/test_fundamental_filter.py` — pass/fail/missing-data cases for both new thresholds, mirroring existing `revenue_growth`/`peg_ratio` tests.
- `tests/test_csp_scanner_integration.py` — end-to-end scan with the preset values applied.
- `tests/test_market_data_store.py` — new-column migration/round-trip case.

## Known Limitation (not a blocker)

The existing `peg_ratio` field reads yfinance's `trailingPegRatio`, which despite the name blends trailing P/E with forward growth estimates — close to, but not strictly identical to, the post's "forward PEG." Since it's the field already used everywhere else in this scanner, this spec reuses it rather than introducing a second PEG variant.

## Out of Scope

- The 14-day "sandbox" cooling-off timer described in the post.
- The AI-generated qualitative "reasoning" narrative per ticker.
