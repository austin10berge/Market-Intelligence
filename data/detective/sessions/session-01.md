# Session 01 — Initial Analysis Run
**Date:** 2026-06-18
**Dataset:** 281 prime observations, 59,863 control observations (60,144 total rows across 36 dates, Sep–Dec 2025)

---

## Key Findings

### Feature Rankings (Top Discriminators by KS Statistic)

The Kolmogorov-Smirnov test ranks features by how differently they distribute between prime and control populations. The top results are unambiguous: **long-term trend position dominates everything else.**

| Rank | Feature | KS Stat | Prime Mean | Control Mean |
|------|---------|---------|------------|--------------|
| 1 | price_vs_ema200_pct | 0.337 | +13.9% | +8.9% |
| 2 | pct_from_52wk_high | 0.325 | -8.1% | -16.3% |
| 3 | price_vs_ema150_pct | 0.304 | +10.7% | +7.1% |
| 4 | price_vs_sma200_pct | 0.299 | +16.4% | +11.8% |
| 5 | price_vs_sma150_pct | 0.295 | +14.4% | +10.4% |
| 6 | sma50_above_sma200 | 0.294 | 100% | 70.6% |
| 7 | price_above_sma200 | 0.284 | 95.7% | 67.3% |
| 8 | price_above_ema200 | 0.275 | 93.9% | 66.4% |
| 9 | ema50_above_ema200 | 0.265 | 96.4% | 70.0% |
| 10 | sma50_above_sma150 | 0.263 | 98.9% | 72.7% |

**Notable observations:**

- `sma50_above_sma200` is the only boolean that returns a prime_mean of exactly 1.0 — all 281 prime picks, without exception, had SMA50 above SMA200 at the time of selection. This is the single strongest binary filter we have.

- `pct_from_52wk_high` moves in the opposite direction from the others: prime stocks are only 8.1% off their 52-week high vs 16.3% for the control universe. Prime state stocks are bought near their highs, not beaten-down recoveries.

- `price_vs_ema200_pct` shows prime stocks trade ~56% higher above their 200-day EMA than control (13.9% vs 8.9%). This range (roughly 2–27%) seems to be a sweet spot — too far above and it's extended, too close and it's not in a trend.

- `bb_width_pct` ranks 12th (KS=0.253) with prime mean 10.3% vs control 15.0%. Prime stocks are in **tighter Bollinger Bands** — they're trending steadily with lower volatility dispersion, not in wide-range chop or breakout explosions.

- Short-term signals (RSI, EMA20/50, price vs SMA20/50) rank significantly lower (KS 0.11–0.19). RSI especially is almost indistinguishable (KS=0.113, prime mean 52.7 vs control 51.5). Prime state is primarily a **structure / trend-position phenomenon**, not a momentum timing one.

- ADX ranks 20th (KS=0.163). Interestingly, prime stocks have slightly *lower* ADX (22.3 vs 24.1), suggesting prime state isn't about the strongest trending stocks — it may be about stocks already in established trends, not ones in explosive new trends.

---

### Criteria Candidates (from Pass 2/3 of analyze.py)

The auto-generated candidates all share the same boolean base: `sma50_above_sma200 + price_above_sma200 + price_above_ema200 + ema50_above_ema200 + sma50_above_sma150`, then differ only in which numeric feature they constrain.

| Rank | Precision | Recall | TP | FP | Criteria Detail |
|------|-----------|--------|----|----|-----------------|
| 1 | 0.9% | 79.4% | 223 | 24,518 | price_vs_ema200_pct: 2.21–26.57% |
| 2 | 0.9% | 79.0% | 222 | 24,635 | price_vs_sma200_pct: 2.69–32.59% |
| 3 | 0.9% | 78.7% | 221 | 24,855 | price_vs_sma150_pct: 2.04–29.62% |
| 4 | 0.9% | 79.0% | 222 | 25,325 | price_vs_ema150_pct: -0.01–21.71% |
| 5 | 0.9% | 76.9% | 216 | 24,849 | pct_from_52wk_high: 2.12–17.9% |

Precision is very low (~0.9%) because we're screening 60k+ stock-days against 281 prime observations. The control-to-prime ratio is ~213:1, so even a filter that cuts 70% of controls will have low precision. **This is expected and not a failure** — the validate.py is showing us how many false positives we'd generate in a real daily scan.

---

### Manual Validation Runs

**Run 1 — Spec default (ema20/50 + RSI + ADX):**
```json
{"price_above_ema50": true, "ema20_above_ema50": true, "rsi_min": 40, "rsi_max": 70, "adx_min": 15}
```
- Precision: 0.8% | Recall: 65.5% | TP: 184 | FP: 22,599
- Observation: This misses 97 prime tickers. The EMA50 filter is too restrictive — many prime picks were below their EMA50 at the time (early in a recovery). RSI=40-70 cuts some valid breakouts.

**Run 2 — Data-driven, top KS features:**
```json
{"sma50_above_sma200": true, "price_above_sma200": true, "ema50_above_ema200": true, "sma50_above_sma150": true, "price_vs_ema200_pct_min": 2.21, "price_vs_ema200_pct_max": 26.57, "rsi_min": 35, "rsi_max": 75, "adx_min": 15}
```
- Precision: 1.1% | Recall: 76.5% | TP: 215 | FP: 19,424
- Better recall (+11%), better precision (+0.3%). Using 200-day MA structure instead of 50-day structure is the right call.

**Observations on missed primes:**
- Many misses are stocks like ANET, GE, HWM, GOOG that had RSI above 65 at selection time — the upper RSI bound of 75 is still catching some but not all. ANET specifically appeared 5+ times in the missed list, consistently at RSI 53-67.
- FLR, INTU, DIS were missed partly because they were below EMA50 when selected — they were early-stage reaccumulation picks, not yet in clean uptrends by MA metrics.
- The criteria will always miss some legitimate picks. The system is designed to flag candidates for review, not to perfectly reproduce the dataset.

---

## First Criteria Hypothesis (Session 01)

Based on this run, the recommended starting filter for daily scanning:

```json
{
  "sma50_above_sma200": true,
  "price_above_sma200": true,
  "ema50_above_ema200": true,
  "sma50_above_sma150": true,
  "price_vs_ema200_pct_min": 2.0,
  "price_vs_ema200_pct_max": 30.0,
  "pct_from_52wk_high_max": 20.0,
  "bb_width_pct_max": 14.0,
  "adx_min": 15
}
```

Rationale:
- The four boolean MAs are non-negotiable — they capture the "stocks in established uptrend" structure that defines all prime picks
- The EMA200 percentage range (2–30%) filters out flat movers and extended blowoffs
- 52wk high distance cap at 20% keeps focus on near-high stocks (prime mean is 8.1%)
- BB width below 14% selects tighter-trading stocks vs volatile chop
- ADX floor at 15 keeps some directional filter without being too restrictive
- RSI left open for now — the feature is nearly indistinguishable between populations and adds FP cuts without meaningful TP improvement

**Next session:** Test the hypothesis above, then try adding `volume_ratio_min: 0.8` and tighter `price_vs_ema200_pct_max: 25` to reduce false positives further.
