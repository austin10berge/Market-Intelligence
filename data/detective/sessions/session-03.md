# Session 03 — 2026-06-19

## Universe Analysis

Checked `universes` column in `universe_fundamentals` for the 74 unique prime tickers:

| Universe      | Prime tickers in it | % of 74 |
|---------------|---------------------|---------|
| sp500         | 60                  | 81%     |
| nyse_large    | 48                  | 65%     |
| nasdaq_large  | 26                  | 35%     |
| nasdaq100     | 18                  | 24%     |

**Conclusion:** ~81% of prime tickers are S&P 500 members. The scanner almost certainly operates against a curated universe of large-caps (nyse_large ∪ nasdaq_large ≈ 500-600 stocks).

When we restrict the control universe to S&P 500 members only:
- S&P 500 control tickers: ~443 non-prime rows
- Full universe control: 2,081 non-prime rows

---

## Prime Ticker Fundamental Profile

| Feature        | p10     | p25     | median  | p75     | p90     |
|----------------|---------|---------|---------|---------|---------|
| market_cap_b   | $15.8B  | $31.2B  | $99.8B  | $273.5B | $882B   |
| beta           | 0.62    | 0.90    | 1.19    | 1.37    | 1.78    |
| revenue_growth | 0.0%    | 6%      | 13%     | 21%     | 50%     |
| earnings_growth| -30%    | 11%     | 29%     | 82%     | 160%    |
| dividend_yield | 0.36%   | 0.66%   | 1.08%   | 1.97%   | 2.76%   |
| forward_pe     | 9.59    | 11.20   | 16.02   | 21.51   | 35.65   |
| peg_ratio      | 0.63    | ~1.0    | 1.49    | ~2.2    | 3.09    |
| fcf (B)        | varies  | —       | —       | —       | —       |

**Key observations:**
- market_cap_b p25 = $31B → using min=30 cuts ~25% of primes
- market_cap_b p10 = $15.8B → using min=15 cuts only ~10% of primes  
- earnings_growth p25 = 11% → min=0.10 cuts ~25% of primes
- earnings_growth p10 = -30% → min=0.05 cuts maybe 15% of primes
- dividend_yield p90 = 2.76% → max=2.5 cuts ~10% of primes
- forward_pe and peg_ratio: distributions too similar to control to be useful filters

---

## Criteria Runs This Session

### Run: v3 — p10/p90 prime bounds with fundamentals

```json
{
  "sma50_above_sma200": 1,
  "market_cap_b_min": 15,
  "beta_min": 0.6,
  "beta_max": 1.8,
  "revenue_growth_min": 0.0,
  "earnings_growth_min": 0.10,
  "dividend_yield_max": 2.8,
  "forward_pe_min": 8,
  "forward_pe_max": 36,
  "price_vs_ema200_pct_min": 2,
  "price_vs_ema200_pct_max": 26,
  "pct_from_52wk_high_max": 15
}
```

Full universe:   Precision=**4.2%** | Recall=31.0% | TP=87 | FP=1,982
S&P500 subset:   Precision=**5.5%** | Recall=31.0% | TP=87 | FP=1,490

Recall too low — p10/p90 fundamental bounds are too aggressive combined.

### Run: v4 — Tighter beta + stricter fundamentals

```json
{
  "sma50_above_sma200": 1,
  "market_cap_b_min": 15,
  "beta_min": 0.7,
  "beta_max": 1.6,
  "revenue_growth_min": 0.05,
  "earnings_growth_min": 0.10,
  "dividend_yield_max": 2.0,
  "price_vs_ema200_pct_min": 2,
  "price_vs_ema200_pct_max": 26,
  "pct_from_52wk_high_max": 15
}
```

Full universe:   Precision=**4.3%** | Recall=21.7% | TP=61 | FP=1,356
S&P500 subset:   Precision=**5.3%** | Recall=21.7% | TP=61 | FP=1,090

Recall is worse — beta range and revenue_growth cuts too many primes.

---

## Key Finding: Recall vs Precision Tradeoff

The precision improvements (4-5%) come at severe recall cost (21-31%). The problem:
- market_cap_b_min=30 cuts ~25% of primes (recall impact: 69.8% → 62.6%)
- earnings_growth_min=0.10 cuts ~15-25% more primes
- beta range cuts additional primes
- These stack multiplicatively, collapsing recall to 21-31%

**To maintain recall ≥ 70%:** must limit total prime attrition across all fundamental filters to <10%, meaning:
- market_cap_b_min=15 (only cuts ~10% of primes at p10) OR
- market_cap_b_min=10 (cuts even fewer)
- At most ONE additional fundamental filter

---

## Bug Fix: NULL-Tolerant _max in _apply_criteria

`_apply_criteria` previously treated NULL values as failing for BOTH `_min` and `_max` filters.
This was correct for `_min` (we can't confirm the floor is met), but wrong for `_max`
(unknown value doesn't violate a ceiling). Impact:

- `dividend_yield`: **45.6% null rate** in prime rows → using `dividend_yield_max` crushed recall from 68.7% to 37%
- `fcf`: 20.3% null rate → similarly broken
- `earnings_growth`: 6% null rate → slightly broken

**Fix** (`analyze.py:_apply_criteria`): For `_max` filters, NULL values now pass through:
```python
feat_val = row.get(feat)
if feat_val is not None and feat_val > val:
    return False
```

After this fix, `dividend_yield_max` only excludes tickers with KNOWN high dividend yields.

---

## New Criteria Runs (this session)

All runs below use the rebuilt pipeline image with the NULL-tolerant `_max` fix.

### Null rates in prime fundamental fields

| Field           | Null rate |
|-----------------|-----------|
| market_cap_b    | 0%        |
| beta            | 0%        |
| revenue_growth  | 0%        |
| earnings_growth | 6%        |
| fcf             | 20.3%     |
| dividend_yield  | 45.6%     |

→ Only `market_cap_b`, `beta`, and `revenue_growth` are safe to use as `_min` filters.
→ `dividend_yield_max` and `fcf_max` can be used as `_max` filters (NULL-tolerant), but not `_min`.

### Key prime technical distributions (by row, not unique ticker)

| Feature              | p10    | p25    | median | p75    | p90    | p95    |
|----------------------|--------|--------|--------|--------|--------|--------|
| price_vs_ema200_pct  | 2.21%  | 7.92%  | 12.15% | 20.26% | 26.57% | 31.46% |
| pct_from_52wk_high   | 2.12%  | 3.52%  | 6.74%  | 10.88% | 17.90% | 20.11% |
| market_cap_b         | $27.7B | $62.7B | $213B  | $882B  | $2814B | $5100B |
| beta                 | 0.62   | 0.98   | 1.22   | 1.39   | 1.78   | 2.20   |
| revenue_growth       | 5%     | 7%     | 14%    | 22%    | 37%    | 85%    |

Note: Market cap values above are row-weighted (large-caps appear many times). Per unique-ticker:
7 prime tickers have market_cap_b < $15B: AAL, EMBJ, FLR, PINS, TME, TOL, WYNN

### Run: v5 — market_cap≥15 + trend (baseline established)

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 15, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 26, "pct_from_52wk_high_max": 15}
```
Precision: 1.7% | Recall: 68.7% | FP: 11,437 — good starting point

### Run: v6 — wider bounds (no fundamentals)

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 10, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 30, "pct_from_52wk_high_max": 18}
```
Precision: 1.5% | Recall: 79.0% | FP: 15,132 — good recall, low precision

### Run: v10 — wide bounds + rv20 cap

`rv20` (realized volatility) prime median=0.24 vs control median=0.29, prime p90=0.42 vs control p90=0.63.
Prime tickers cluster at moderate volatility — high-vol control tickers bleed through the trend filters.

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 15, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 35, "pct_from_52wk_high_max": 18, "rv20_max": 0.40}
```
Precision: 1.7% | Recall: 71.2% | FP: 11,444 — first run to clear 70% recall with precision gain

### Run: v11 — looser rv20 cap

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 15, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 35, "pct_from_52wk_high_max": 20, "rv20_max": 0.45}
```
Precision: 1.8% | Recall: 78.6% | FP: 12,148 — high recall

### Run: v12 — v10 + NULL-tolerant dividend cap

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 15, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 35, "pct_from_52wk_high_max": 18, "rv20_max": 0.45, "dividend_yield_max": 2.5}
```
Precision: **2.2%** | Recall: 74.4% | FP: 9,418

### Run: v13 — v12 + revenue_growth≥5% ✅ BEST AT ≥70% RECALL

```json
{
  "sma50_above_sma200": 1,
  "market_cap_b_min": 15,
  "price_vs_ema200_pct_min": 2,
  "price_vs_ema200_pct_max": 35,
  "pct_from_52wk_high_max": 18,
  "rv20_max": 0.45,
  "dividend_yield_max": 2.5,
  "revenue_growth_min": 0.05
}
```
Precision: **2.7%** | Recall: 70.1% | TP: 197 | FP: 7,202 — **3x baseline precision at ≥70% recall**

Top FP sectors: Industrials 1,311 | Financial Services 1,180 | Technology 1,145

### Run: v14 — market_cap≥20 + v12 filters

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 20, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 35, "pct_from_52wk_high_max": 18, "rv20_max": 0.45, "dividend_yield_max": 2.5}
```
Precision: 2.5% | Recall: 74.0% | FP: 8,203

### Run: v16 — market_cap≥20 + all v13 filters

```json
{"sma50_above_sma200": 1, "market_cap_b_min": 20, "price_vs_ema200_pct_min": 2, "price_vs_ema200_pct_max": 35, "pct_from_52wk_high_max": 18, "rv20_max": 0.45, "dividend_yield_max": 2.5, "revenue_growth_min": 0.05}
```
Precision: **3.1%** | Recall: 69.8% | FP: 6,179 — highest precision near 70% recall

---

## Precision/Recall Frontier Summary

| Run   | Precision | Recall | FP     | Key additions vs baseline |
|-------|-----------|--------|--------|--------------------------|
| Orig  | 0.9%      | 69.8%  | 21,662 | trend only               |
| v5    | 1.7%      | 68.7%  | 11,437 | + market_cap≥15          |
| v12   | 2.2%      | 74.4%  | 9,418  | + wider bounds + div cap |
| v15   | 2.5%      | 71.2%  | 7,813  | + rev_growth≥3%          |
| **v13** | **2.7%** | **70.1%** | **7,202** | **+ rev_growth≥5%** ← recommended |
| v16   | 3.1%      | 69.8%  | 6,179  | market_cap≥20            |

---

## Persistent Missed Prime Tickers (across all runs)

- **ANET** — Has very high rv20 (>0.45 on prime dates) AND possibly price_vs_ema200 > 35% at times. Extended momentum growth stock. Scanner has different logic for these.
- **XYZ** — Consistently fails. Likely data quality issue or a very atypical ticker.
- **TME** — Chinese ADR (Tencent Music). Small market cap (~$8-10B), fails market_cap_b_min=15.
- **FLR, WYNN, AAL** — Small/mid-cap primes below $15B threshold.
- **INTU** — Fails in October 2025 deep pullback period. Probably pct_from_52wk_high > 18% OR rv20 spike.
- **AMZN, DIS** — During October 2025 pullback, EMA50+ = 0 AND likely pct_from_52wk_high > 18%. Scanner picks these during deeper pullbacks than our criteria allow.
- **PHM** (PulteGroup) — Revenue growth < 5% (cyclical homebuilder).

---

## Next Steps

1. **Investigate ANET rv20** — check actual rv20 values on prime dates. The scanner likely either uses longer-period RV or doesn't cap volatility for high-momentum tech stocks.

2. **Sector-stratified analysis** — Financial Services contributes 1,082-1,647 FPs even in the best criteria. Consider running KS stats within the Technology and Healthcare sectors where we have many prime picks.

3. **Test v13 against nyse_large ∪ nasdaq_large subset** — If the scanner operates against ~500 tickers, v13's true precision would be roughly 4x higher (~10%+).

4. **Consider relaxing pct_from_52wk_high_max to 25%** — Might recover INTU, AMZN, DIS during pullbacks.

5. **Wire v13 into CSP scanner** as pre-filter gate.


