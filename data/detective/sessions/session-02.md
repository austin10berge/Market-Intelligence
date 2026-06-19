# Session 02 — 2026-06-19

## What Changed
Added 9 fundamental columns to `detective_features`: `market_cap_b`, `beta`, `forward_pe`, `peg_ratio`,
`revenue_growth`, `earnings_growth`, `debt_to_equity`, `dividend_yield`, `fcf`.
All 60,144 existing rows backfilled from `universe_fundamentals` via ALTER TABLE + UPDATE join.
`analyze.py` updated to rank fundamentals alongside technical features.

---

## New KS Rankings (top 10 after adding fundamentals)

| Rank | Feature              | KS    | Prime mean    | Control mean  |
|------|----------------------|-------|---------------|---------------|
| 1    | market_cap_b         | 0.643 | $801B         | $54B          |
| 2    | fcf                  | 0.544 | $10.4B        | $30.9B        |
| 3    | dividend_yield       | 0.367 | 1.30%         | 2.52%         |
| 4    | price_vs_ema200_pct  | 0.337 | +13.9%        | +8.9%         |
| 5    | pct_from_52wk_high   | 0.325 | 8.1% below    | 16.3% below   |
| 6    | price_vs_ema150_pct  | 0.304 | +10.7%        | +7.1%         |
| 7    | price_vs_sma200_pct  | 0.299 | +16.4%        | +11.8%        |
| 8    | price_vs_sma150_pct  | 0.295 | +14.4%        | +10.4%        |
| 9    | beta                 | 0.294 | 1.228         | 1.021         |
| 10   | sma50_above_sma200   | 0.294 | 100%          | 70.6%         |

Note: `forward_pe` (KS≈0.06) and `peg_ratio` (KS≈0.03) are essentially useless as filters.
`revenue_growth` and `earnings_growth` are weak individually (KS < 0.10) but may sharpen combined criteria.

---

## Key Fundamental Findings

**market_cap_b is the dominant signal** — KS=0.643, far above everything else.
Prime picks cluster at $28B–$2800B (p10–p90). The p25 of prime is $31B vs control median of $11.6B.
A single `market_cap_b >= 30` filter cuts the false-positive universe substantially.

**FCF is counterintuitive** — prime_mean ($10.4B) is LOWER than control_mean ($30.9B).
This is because the control universe contains a few massive FCF generators (AAPL, MSFT ~$100B FCF)
that skew the mean up. What the KS stat is detecting is a tighter, moderate FCF distribution
in prime tickers — they're large but not mega-cap FCF dominators. Best used as a range,
not a floor.

**dividend_yield** — prime tickers pay less in dividends (1.3% vs 2.5%).
This filters out most Utilities, REITs, and mature industrials. `dividend_yield_max: 2.0`
is a useful signal without killing recall.

**beta range** — prime tickers have moderately higher beta than control (1.23 vs 1.02).
The scanner avoids very low-beta defensives AND very high-beta speculative names.

**forward_pe and peg_ratio are NOT useful** — near-identical medians and distributions.
Do not include in criteria.

---

## Criteria Runs This Session

### Run: Original (no fundamentals)
```json
{"sma50_above_sma200": true, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 26, "pct_from_52wk_high_max": 15}
```
Precision: 0.9% | Recall: 69.8% | FP: 21,662

### Run: Set A — EMA stack tightened
```json
{"sma50_above_sma200": true, "ema20_above_ema50": true, "price_vs_ema200_pct_min": 5, "price_vs_ema200_pct_max": 22, "pct_from_52wk_high_max": 8}
```
Precision: 0.9% | Recall: 38.8% | FP: 11,666 — recall collapsed, ema20>50 too strict

### Run: Set B — EMA150 + tight 52wk window
```json
{"price_vs_ema200_pct_min": 8, "price_vs_ema200_pct_max": 20, "pct_from_52wk_high_max": 6, "ema150_above_ema200": true}
```
Precision: 1.1% | Recall: 28.1% | FP: 7,147 — recall too low

### Run: market_cap >= 30 added
```json
{"sma50_above_sma200": true, "market_cap_b_min": 30, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 26, "pct_from_52wk_high_max": 15}
```
Precision: **2.3%** | Recall: 62.6% | FP: 7,555 — market_cap alone 2.5x precision improvement

### Run: + earnings_growth >= 5%
```json
{"sma50_above_sma200": true, "market_cap_b_min": 30, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 26, "pct_from_52wk_high_max": 15, "earnings_growth_min": 0.05}
```
Precision: 2.9% | Recall: 54.4% | FP: 5,043 — recall cost too high for the precision gain

### Run: tighter market_cap + dividend cap
```json
{"sma50_above_sma200": true, "market_cap_b_min": 50, "price_vs_ema200_pct_min": 5, "price_vs_ema200_pct_max": 25, "pct_from_52wk_high_max": 12, "dividend_yield_max": 2.0}
```
Precision: **4.7%** | Recall: 26.7% | FP: 1,526 — precision breakthrough but recall too low

---

## Persistent Missed Prime Tickers

These tickers appear as misses across almost every criteria variant — worth investigating individually:

- **ANET** — misses most criteria. Price likely too extended above 200 EMA (>26%) or too close to 52wk high.
- **XYZ** — appears in every missed list. XYZ is an unusual ticker; may be a data quality issue or very atypical chart.
- **NVDA** — misses tight 52wk-high or price_vs_ema200 upper bounds (extended bull run).
- **UAL, SCHW** — frequently have EMA50+=0 (price dips below EMA50 temporarily). Scanner picks them anyway.
- **VST** — BB%B=1.114 (price above upper BB band), which fails most range filters.

---

## Hypothesis: Scanner Uses a Curated Universe

The precision ceiling (~2-5%) even with strong market_cap filters suggests a structural problem:
we're comparing prime picks against 2,244 tickers, but the scanner likely operates against a
curated watchlist (~500 names, probably S&P 500 or similar). Large-cap Financial Services
consistently contributes 20-30% of all FPs — banks in the S&P 500 are prime tickers (JPM, GS, BAC),
but the broader 2244-ticker universe includes hundreds of non-prime large-cap financials.

**If the scanner's universe is ~500 tickers, our control group is ~4x too large**, and the
true precision at scan-time would be roughly 4x higher than what we measure.

---

## Next Steps

1. **Estimate the scanner's actual universe** — pull all tickers with `market_cap_b >= 10` AND
   in `universe_fundamentals.universes` that match S&P 500 or NASDAQ 100 inclusion.
   Re-compute precision/recall against this ~500-ticker subset.

2. **Beta range filter** — try `beta_min: 0.8, beta_max: 1.6` to exclude ultra-defensive and speculative names.

3. **Investigate ANET** — check its price_vs_ema200_pct on missed dates. It's consistently in the CSV
   as a prime pick but keeps failing the technical criteria. May indicate the scanner applies different
   EMA position thresholds for certain sectors.

4. **Run analyze again** with sector-stratified KS stats — the financial services FP flood may be
   masking signals that are strong within other sectors.

5. **Try the `universes` column** in `universe_fundamentals` — check if it contains S&P 500 membership
   flags that could be used as a pre-filter.
