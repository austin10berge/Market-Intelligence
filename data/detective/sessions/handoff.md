# Session Handoff — Algo Detective

**Last updated:** 2026-06-19  
**Resume at:** Session 04

---

## What We're Doing

Reverse-engineering a friend's trading scanner that emits "prime state" tickers — stocks it considers ideal candidates for selling cash-secured puts (CSPs). We have a CSV of 281 (date, ticker) observations from Sep–Dec 2025.

**Repo:** `/home/dev/workspace/Market-Intelligence`  
**Module:** `src/algo_detective/`  
**Data:** `data/detective/prime_tickers.csv` (281 rows, 74 unique tickers, 36 unique dates)  
**DB:** populated SQLite at `settings.db_path` — 60,144 rows in `detective_features`

---

## Current State

### Best criteria found (v13)

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

**Result:** Precision=2.7% | Recall=70.1% | TP=197 | FP=7,202

**Runner-up (v16):** market_cap_b_min=20 instead of 15 → 3.1% precision, 69.8% recall (just under 70% gate)

Baseline for comparison: `sma50_above_sma200 + EMA200 range 2-26% + 52wk≤15%` → 0.9% precision, 69.8% recall

### Key facts about the data

- `sma50_above_sma200 = 1` on **100%** of prime rows — perfect requirement, no prime ever had SMA50 below SMA200
- `dividend_yield`: 45.6% NULL rate in prime rows → use only as `_max` (NULL-tolerant fix applied in commit b0f939f)
- `fcf`: 20.3% NULL rate — same caution
- `earnings_growth`: 6% NULL rate — manageable as `_min`
- `market_cap_b`, `beta`, `revenue_growth`: 0% nulls — safe to use as `_min`
- 7 prime tickers are < $15B market cap: AAL, EMBJ, FLR, PINS, TME, TOL, WYNN

### Persistent misses (appear in almost every run)
- **ANET** — rv20 > 0.45 on prime dates (high-volatility momentum stock). Scanner uses different logic for it.
- **XYZ** — probably a data quality artifact
- **INTU, AMZN, DIS** in Oct 2025 — deep pullback period; pct_from_52wk_high > 18% and/or rv20 spike
- **TME, FLR, AAL, WYNN** — small caps below market_cap threshold

### Universe hypothesis (confirmed)

81% of prime tickers are S&P500 members. Scanner likely screens against nyse_large ∪ nasdaq_large (~500-600 tickers), NOT the full 2,244-ticker universe we compare against. This explains why our precision is low — the control group is ~4x too large.

**Impact if true:** v13's true precision at scan-time ≈ 2.7% × 4 ≈ **10-11%**. Testing against this subset is the single highest-leverage experiment left.

---

## How to Run Things

```bash
cd /home/dev/workspace/Market-Intelligence

# Validate criteria
docker compose run --rm pipeline python -m src.algo_detective.validate \
  --criteria '{"sma50_above_sma200": 1, "market_cap_b_min": 15, ...}'

# Re-run KS analysis
docker compose run --rm pipeline python -m src.algo_detective.analyze

# Build feature matrix (skip already-computed pairs)
docker compose run --rm pipeline python -m src.algo_detective.build

# Run tests
docker compose run --rm test python3 -m pytest tests/test_algo_detective_*.py -v
```

**Important:** After editing any `.py` file, rebuild the pipeline image before running:
```bash
docker compose build pipeline
```
(The test service mounts source live, but the pipeline service uses a baked image.)

---

## Code Structure

| File | Purpose |
|------|---------|
| `src/algo_detective/store.py` | SQLite DDL + CRUD. `ensure_tables()`, `get_all_features()`, `backfill_fundamentals()` |
| `src/algo_detective/features.py` | `compute_features(ticker, date, df, sector)` → 50+ indicators |
| `src/algo_detective/universe.py` | `get_control_tickers(date, exclude)` and batch OHLCV load |
| `src/algo_detective/build.py` | CLI orchestrator. `--backfill-fundamentals` flag. |
| `src/algo_detective/analyze.py` | KS ranking (`rank_features`), threshold search (`find_thresholds`), `_apply_criteria` |
| `src/algo_detective/validate.py` | `validate_criteria(criteria)` → precision/recall/FP-by-sector/missed-primes |
| `src/algo_detective/ingest.py` | CSV parser → `PrimeTicker` dataclass |

### _apply_criteria semantics (analyze.py:79)
- `_min` keys: NULL fails (we can't confirm floor is met)
- `_max` keys: NULL passes (unknown doesn't violate ceiling) — **fixed in b0f939f**
- `bool/int` keys: exact match required

---

## Suggested Next Steps (priority order)

### 1. Test v13 against the scanner's likely universe (highest leverage)

The `universe_fundamentals` table has a `universes` column with membership tags: `sp500`, `nyse_large`, `nasdaq_large`, `nasdaq100`.

Run validate against only `nyse_large ∪ nasdaq_large` control tickers:

```python
from src.algo_detective.store import get_all_features
features = get_all_features()

# Restrict control to large-cap universe
import sqlite3
from src.algo_detective.store import _get_connection
conn = _get_connection()
large_caps = set(
    r[0] for r in conn.execute(
        "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%nyse_large%' OR universes LIKE '%nasdaq_large%'"
    ).fetchall()
)
conn.close()

filtered = [f for f in features if f['is_prime'] == 1 or f['ticker'] in large_caps]
```

Then call `validate_criteria(v13_criteria, features=filtered)`.

Expected result: precision jumps to ~10-15% (4x uplift from using the correct universe).

### 2. Add options-specific features

The CSV has: `iv`, `delta`, `premium`, `annual_yield_pct`, `pop_pct`, `cushion_pct`, `spread_pct`. These are AT THE TIME OF SELECTION. The scanner may be filtering on options characteristics, not just stock technicals:

- **IV floor**: Scanner probably requires IV ≥ some threshold to generate a meaningful CSP premium. The CSV `iv` column could let us extract the IV at selection time.
- **IV/RV ratio**: If IV > RV, the CSP is "expensive" — good for sellers. Check `iv` (from CSV) vs `rv20` (computed) for each prime pick.
- **Liquidity**: High-IV stocks tend to be more liquid. `volume_ratio` might correlate.

Build a feature that computes `iv / rv20` for each (date, ticker) pair using the CSV's IV column joined to the feature matrix.

### 3. Sector-stratified KS stats

Financial Services consistently generates 1,000-2,000 FPs. But many prime picks ARE financials (JPM, GS, BAC). Before filtering the sector entirely:

- Compute KS stats WITHIN Financial Services: prime financials vs control financials
- Same for Technology (many prime picks, many FP tech stocks too)
- This might reveal sector-specific thresholds (e.g., financials need higher market_cap, tech needs lower dividend_yield)

```python
from src.algo_detective.analyze import rank_features
features = get_all_features()
fin_features = [f for f in features if f.get('sector') == 'Financial Services']
rankings = rank_features(fin_features)
```

### 4. Investigate ANET's rv20

ANET is in the prime list for 8+ dates but fails every criteria set because rv20 > 0.45.

```python
features = get_all_features()
anet = [f for f in features if f['ticker'] == 'ANET' and f['is_prime'] == 1]
for f in sorted(anet, key=lambda x: x['date']):
    print(f["date"], f["rv20"], f["price_vs_ema200_pct"], f["pct_from_52wk_high"])
```

If rv20 is consistently 0.5-0.8 for ANET, the scanner doesn't cap volatility for momentum stocks. This suggests a sector-specific exception OR the scanner uses a different volatility measure (e.g., IV from options chain, not historical RV).

---

## Session Notes

- `data/detective/sessions/session-01.md` — initial KS results, first criteria explorations
- `data/detective/sessions/session-02.md` — added fundamentals, market_cap as dominant signal
- `data/detective/sessions/session-03.md` — NULL-tolerant _max fix, precision/recall frontier, v13 as best balanced criteria

---

## Open Question: Is the Current Methodology Sufficient?

See the companion discussion in the session that created this handoff. Short version:

**Current approach is converging but hitting a structural ceiling.** The three breakthrough moves are:
1. Correct universe (nyse_large ∪ nasdaq_large) — most impactful, can do now
2. Options-specific features from the CSV (iv, PoP%, cushion%) — requires new feature engineering
3. ML classifier on the full feature matrix — would find non-linear combinations our grid search misses
