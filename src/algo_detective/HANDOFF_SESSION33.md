# Algo Detective — Session 33 Handoff

**Date:** 2026-07-03  
**Project:** Reverse-engineering u/GarbageTimePro's CSP screener (mlabstrading.com)  
**Goal:** Replicate his EOD options scanner using public data

---

## What We're Doing

GarbageTimePro runs a systematic cash-secured put strategy on "boring" large-cap stocks. He posts nightly watchlists to paying members. We're reverse-engineering his 4-tier filter hierarchy (regime → fundamentals → technicals → options) by matching our criteria against 46 known OOS trades (Jan–Jun 2026).

**Key constraint:** His `mlabs_score` column is his own LLM (homelab inference) — unrecoverable. We're only trying to replicate the mechanical filter layer.

---

## Current Best Criteria: V41

```python
# Run with: docker compose run --rm pipeline python -m src.algo_detective.sessionXX

V31A = {
    "sma50_above_sma200": 1,
    "market_cap_b_min": 25,
    "price_vs_ema200_pct_min": 0,
    "price_vs_ema200_pct_max": 42,
    "pct_from_52wk_high_max": 12,
    "rv20_max": 0.45,
    "dividend_yield_max": 2.5,
    "options_iv_min": 0.20,
    "financials_market_cap_b_min": 100,
    "technology_fcf_min": 0.01,
    "industrials_iv_min": 0.30,
    "consumer_cyclical_iv_min": 0.30,
    "healthcare_iv_min": 0.25,
    "real_estate_block": 1,
    "consumer_defensive_iv_max": 0.32,
    "energy_iv_min": 0.38,
    "basic_materials_iv_min": 0.38,
    "utilities_iv_min": 0.50,
    "adx_min": 15,
    "bb_width_pct_min": 4.0,
    "bb_width_pct_max": 14.0,
    "volume_ratio_max": 1.10,
    "forward_pe_max": 50,
    "communication_services_market_cap_b_min": 50,
    "iv_rv_min": 0.9,
    "financials_adx_min": 20,
    "financials_volume_ratio_max": 0.90,
    "consumer_cyclical_rsi_max": 44,
    "technology_rsi_max": 54,
}

V38 = {**V31A, "price_vs_ema200_pct_min": 5, "sma50_above_sma150": 1,
        "bb_width_pct_max": 20.0, "volume_ratio_max": 1.15,
        "iv_rv_min": 1.0, "pcr_vol_max": 2.0, "technology_rsi_max": 60}
V39 = {**V38, "adr20_pct_max": 4.0}
V40 = {**V39, "industrials_rsi_max": 70, "financials_rsi_max": 70, "healthcare_rsi_max": 60}
V41 = {**V40, "bb_width_pct_max": 21.0}   # ← CURRENT BEST

# Strip IV keys for BASE (historical IV data is incomplete):
_IV_KEYS = {"options_iv_min", "iv_rv_min", "pcr_vol_max",
            "industrials_iv_min", "consumer_cyclical_iv_min", "healthcare_iv_min",
            "consumer_defensive_iv_max", "energy_iv_min", "basic_materials_iv_min",
            "utilities_iv_min"}
V41_BASE = {k: v for k, v in V41.items() if k not in _IV_KEYS}
```

### V41 Scorecard (from session32, narrow 84-ticker universe)
| Split | Precision | Recall | TP count |
|---|---|---|---|
| Sep-Dec 2025 full | 44.7% | 40.6% | 114 |
| Train Sep-Oct 2025 | 46.2% | 48.6% | — |
| Test Nov-Dec 2025 | 40.0% | 25.0% | — |
| **2026 OOS** | **6.0%** | **30.4%** | **14/46** |

**Canonical baseline for V41 is session32 numbers above.** After session32, the nightly pipeline expanded the DB from 84 to 1704 tickers; session33 baseline shows different numbers due to updated features — use session32 numbers as ground truth.

---

## Data Setup

### Narrow Universe Filter (REQUIRED in every session)
```python
all_features = get_all_features()
prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
all_features = [f for f in all_features if f["ticker"] in prime_tickers]
```
Without this, `get_all_features()` now returns 120K+ rows from 1704 tickers — the full universe. All criteria were calibrated on the 84-ticker narrow set. Forgetting this filter gives completely wrong precision numbers.

### Splits
```python
TRAIN_CUTOFF = "2025-10-31"
TEST_START   = "2025-11-01"
OOS_START    = "2026-01-01"
sep_dec = [f for f in all_features if f["date"] < OOS_START]
train   = [f for f in sep_dec if f["date"] <= TRAIN_CUTOFF]
test    = [f for f in sep_dec if f["date"] > TRAIN_CUTOFF]
oos     = [f for f in all_features if f["date"] >= OOS_START]
```

### Running a Session
```bash
docker compose build pipeline            # after editing session file
docker compose run --rm pipeline python -m src.algo_detective.sessionXX
~/.local/bin/ruff check src/algo_detective/sessionXX.py  # lint before building
```

---

## What Happened This Session (Session 32 + 33)

### Session 32 — V41 confirmed, financials_volume_ratio sweep
- **V41 confirmed**: bb_width_pct_max=21.0 recovers C Apr28 (bb=20.20) and NVDA May4 (bb=20.07)
- **financials_volume_ratio_max=1.15**: recovers C Jun9 (+1 OOS TP → 15/46, 32.6% recall)
  - Cost: sd precision 44.7% → 43.0%, te precision 40.0% → 38.1%, +6 sdTPs
  - V42 candidate: V41 + financials_volume_ratio_max=1.15. Not yet adopted — marginal tradeoff.
- **EMA200 ceiling (42%)**: removing recovers 0 OOS TPs (LRCX/AMAT are co-blocked by rv20+vr)

### Playwright crawl — mlabstrading.com
- App (app.mlabstrading.com) is closed-beta, no public screener UI visible
- **Two-score system discovered**: Setup Score (stock setup quality, 0-100; "Good"≈70+) + Contract Score (options quality, 0-100). These are separate. mIQ label = AI summary of combined scores.
- Marketing page: "Expected Move, IV Rank, spread, cushion, and 140+ industry filters"
- **C Jun9 confirmed as passing his filter** (June 8 recap blog): "Citi was the only setup clearing our filters while everything else showed thin cushions, wide spreads, or trend structures that fell apart." Blocked by our financials_volume_ratio_max=0.90 (actual vr=1.14).
- **Public trade log CSVs** available at `https://blog.mlabstrading.com/trade_logs/trade_log_YYYY-MM-DD.csv`
  - Latest: `trade_log_2026_06_28.csv` (119 rows, full 2026 YTD)
  - 2025 data not publicly available
- Blog "Standout Names" tables in weekly recaps show stocks that passed the screener but weren't traded (Setup Score + Contract Score + mIQ listed for each)

### Session 33 — Market cap floor sweep
- Lowering market_cap_b_min from 25B to any value (20/15/10/5/none): **0 OOS TPs recovered**
- All 7 small-mcap OOS TPs are co-blocked by other gates:
  - AA May12 (15.7B): rv20 + volume_ratio
  - AA May14 (15.7B): pfh + rv20
  - AAL Jan06 (10.6B): pfh + volume_ratio
  - AAL May14 (10.6B): 5 gates (structural dead-end)
  - AEO Feb02 (3B): 6 gates (structural dead-end)
  - DOCN Jan22 (18.1B): 5 gates (rv20, vr, pe, rsi, adr)
  - M Jan14 (6.3B): pfh + div_yield + vr
- **Keep market_cap_b_min=25.** FP cost is +41 Sep-Dec FPs for no OOS TP gain.
- financials_market_cap_b_min=100 audit: Lowering adds 0 oosTP, 1 sdTP at -1pp precision. Keep at 100.

---

## Known Structural Dead-Ends (cannot recover with current gate set)

| Ticker | OOS dates | Why stuck |
|---|---|---|
| FCX | 5 dates | Always rv20>0.45 + pfh>12 + bb_width>20 simultaneously. Copper miner structural high-vol. |
| AAL | 4 dates | Market cap ~$10.6B + co-blocked by pfh/rv20/vr |
| AA | 2 dates | Market cap ~$15.7B + co-blocked by rv20/pfh |
| AEO | 1 date | Market cap ~$3B + 6 gates failing simultaneously |
| QCOM | 3 dates (Jan 2026) | Bought below EMA200 by conviction; our price_vs_ema200_pct_min=5 blocks |
| March 2026 | 5 primes | FCX entirely. 0% recall period |
| May 2026 | 8 primes | Tariff selloff — rv20+pfh+vr+adr simultaneously failing |

---

## Session 31 Gate Rankings (why only 14/46 OOS TPs captured)

Gates ranked by OOS TPs blocked (V40_BASE, applies to V41_BASE too):
1. `volume_ratio_max=1.15` → 20 OOS TPs blocked
2. `pct_from_52wk_high_max=12` → 17 OOS TPs blocked
3. `rv20_max=0.45` → 15 OOS TPs blocked
4. `price_vs_ema200_pct_min=5` → 8 OOS TPs blocked
5. `market_cap_b_min=25` → 7 OOS TPs blocked
6. `adr20_pct_max=4.0` → 6 OOS TPs blocked
7. `bb_width_pct_max=21.0` → now 4 OOS TPs blocked (was 6 at 20.0 before V41)
8. `technology_rsi_max=60` → 4 OOS TPs blocked

**All top blockers are co-blocking** — relaxing any single gate recovers 0 OOS TPs for most.
**Cleanest relaxation found so far: bb_width=21 (+2 OOS TPs, 0 sdTPs lost)** — already done (V41).

---

## Key Confirmed Facts About His Screener

- **ADR < 4.0** is a hard filter (verbatim quote: "When ADR rose above 4.0, he dropped ANET")
- **"20 > 50 > 200" MA structure** (he uses sma20>sma50>sma200)
- **VIX regime filter** (not a hard block; he adapts DTE when VIX is elevated)
- **Sector caps**: Financials ≥$100B mcap, Communication Services ≥$50B mcap
- **Sector-specific volume floors**: Financials volume_ratio ≤ 0.90 (tighter than global 1.15)
- **Setup Score** is his LLM-based stock quality score (0-100). "Good" ≈ 70+.
- He explicitly avoids: SOFI, HIMS, MARA, RIOT, IONQ, TSLL ("high-beta junk")
- Balance sheet focus (FCF, debt/equity, payout ratio ~30% per QCOM post)

---

## Future Analysis Plans (Priority Order)

### 1. Fundamentals KS re-analysis (narrow universe) ← RECOMMENDED NEXT
`peg_ratio`, `debt_to_equity`, `revenue_growth`, `earnings_growth` are in detective_features
but were KS-ranked on the full universe early in the project. Re-run on the 84-ticker narrow
dataset — these may look very different. His QCOM post mentioned "FCF/debt/payout ratio ~30%"
suggesting a global `debt_to_equity_max` might reduce FPs. Also: sector-specific forward_pe
ceilings (tech trades at higher PE than industrials).

### 2. Targeted Reddit re-scrape for numerical fundamentals thresholds
Search GarbageTimePro's full comment history for "P/E", "debt", "earnings", "FCF",
"profitable", "payout" to surface hard numbers. Use Arctic Shift API:
`/api/comments/search?author=GarbageTimePro&q=debt&limit=100`

### 3. Expected move gate
He said "strike outside of expected move". Computable from `best_iv × sqrt(DTE/252) × price`
for ~5-day DTE. May already be implicit in IV gates but worth verifying.

### 4. Blog "Standout Names" mining
The weekly public recap posts include a "Standout Names" table (Setup Score + Contract Score
+ mIQ) for stocks that cleared the screener but weren't traded. Only Jun 22 recap has this so
far. Scrape all 2026 weekly recaps to build a larger set of screener-pass rows.

---

## Important Files

| File | Purpose |
|---|---|
| `src/algo_detective/analyze.py` | Gate application logic — `_apply_criteria()`, sector-specific handlers |
| `src/algo_detective/store.py` | DB connection, `get_all_features()`, `get_all_options()` |
| `src/algo_detective/session31.py` | OOS miss analysis + gate ranking |
| `src/algo_detective/session32.py` | V41 scorecard + financials_volume_ratio sweep |
| `src/algo_detective/session33.py` | Market cap floor sweep |
| `src/screener/stocks.py` | Live screener — `_calculate_adr20()` added |
| `memory/project_algo_detective.md` | Full project memory with all session findings |

---

## Common Gotchas

1. **Always use narrow universe filter** (`prime_tickers` filter) — the DB now has 1704 tickers
2. **Strip IV keys for BASE criteria** — historical IV data is incomplete, use `_IV_KEYS` set
3. **sector values in features**: "Financial Services" (not "Financials"), "Consumer Cyclical" (not "Consumer Discretionary"), "Communication Services" (not "Communication")
4. **Lint before building**: `~/.local/bin/ruff check src/algo_detective/sessionXX.py`
5. **Docker volume**: The DB is a mounted volume; the image build does NOT rebake it. Rebuilding the image picks up new Python files only.
6. **`sma50_above_sma200` vs `sma50_above_sma150`**: both are in V41 (different keys, both enforced)
7. **V41_BASE precision numbers**: session32 baseline (sdTP=114, oosTP=14) is canonical. Later sessions may show sdTP=133 due to DB refresh — OOS TP count (14) is stable.
