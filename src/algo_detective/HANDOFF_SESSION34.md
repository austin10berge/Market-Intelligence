# Algo Detective — Session 34 Handoff

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

**Canonical baseline for V41 is session32 numbers above.** After session32, the nightly pipeline expanded the DB from 84 to 1704 tickers; later sessions show sdTP=133 due to updated features — use session32 numbers as ground truth.

---

## Data Setup

### Narrow Universe Filter (REQUIRED in every session)
```python
all_features = get_all_features()
prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
all_features = [f for f in all_features if f["ticker"] in prime_tickers]
```
Without this, `get_all_features()` now returns 120K+ rows from 1704 tickers. Forgetting this filter gives completely wrong precision numbers.

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

## What Happened This Session (Session 34)

### Fundamentals KS re-analysis on the narrow 84-ticker universe

**debt_to_equity_max — dead end:**
- Stored values are in yfinance percentage units (×100), NOT as ratios:
  - NVDA=6.55 means 6.55% = 0.066 actual ratio (very low debt)
  - WMT=74.82 means 74.82% = 0.748 ratio (moderate)
  - UAL=195.08 means 195% = 1.95 ratio (highly leveraged, expected for airlines)
- OOS TPs span the full range: NVDA (6.55) → UAL (195) → DG (178) → DAL (105) → WMT (74.8)
- Any threshold below ~200 kills multiple genuine OOS trades
- Sep-Dec FPs also span this range: GS=678, MS=502, IBM=211 but ALSO AAPL=79.55, NVDA=6.55
- **Conclusion: do NOT add debt_to_equity_max gate. His debt assessment is qualitative per his blog.**

**peg_ratio_max — dead end:**
- UAL has peg=6.50, WMT peg=4.61, DVN peg=2.92 — all genuine OOS trades
- Any threshold below 7.0 blocks at least 1 OOS TP
- peg_max=3.0 keeps 11/14 OOS TPs, peg_max=5.0 keeps 13/14 — still costly
- **Conclusion: do NOT add peg_ratio_max gate.**

**forward_pe_max=45 — V42 candidate (strong):**
- Keeps all 14 OOS TPs (forward_pe_max=40 also keeps 14 OOS TPs!)
- pe_max=45: removes 22 Sep-Dec FPs, −9 sdTPs, **+0.4pp sd precision** — net positive
- pe_max=40: removes 34 FPs but also −24 sdTPs, −1.3pp sd precision — marginal
- Sep-Dec TP forward_pe distribution: p10=9.64, p50=16.85, p90=38.50 — well within ≤45
- Tickers near the ceiling: LRCX (49.7), HWM (46.4) are the Sep-Dec TPs lost at pe_max=45
- LRCX has prime_days=2 in Sep-Dec; HWM has prime_days=4 — both low-frequency
- **Conclusion: V42 = V41 + forward_pe_max=45. Need to verify OOS TPs individually.**

**earnings_growth — interesting signal, not yet swept:**
- KS strengthens Sep-Dec→OOS: 0.095 → 0.245 (same pattern as price/SMA gates that matter)
- OOS TP median: 0.794 (79% YoY EG) vs FP median: 0.289 (29%)
- Sep-Dec TP median: 0.244 (similar to Sep-Dec FPs at 0.293) — weak in-sample
- This OOS strengthening suggests 2026 he specifically targeted strong-earnings names
- **Session 35 candidate: earnings_growth_min sweep on V41_BASE**

**fcf — informative but coverage issues:**
- KS=0.205 (Sep-Dec), 0.265 (OOS) — strengthens
- TP median fcf=4.092 vs FP median=1.710 in Sep-Dec; 2.772 vs 1.883 in OOS
- Coverage: only 85.7% — 14% of rows have NULL fcf (some tickers missing)
- `technology_fcf_min=0.01` already in V41 (positive fcf required for Tech only)
- A global `fcf_min` might be worth testing but NULL handling needs care

---

## V42 Candidate

```python
V42 = {**V41, "forward_pe_max": 45.0}
V42_BASE = {k: v for k, v in V42.items() if k not in _IV_KEYS}
```

Expected from session34:
- sd: ~34.3% precision (+0.4pp vs V41), ~44.1% recall (−3.2pp)
- oos: same 14 TPs, 30.4% recall
- Net: removes 22 Sep-Dec FPs, loses 9 sdTPs (LRCX, HWM mostly) — slightly positive

Needs full 4-split confirmation in session35 before adopting.

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

## Future Analysis Plans (Priority Order)

### 1. V42 confirmation (forward_pe_max=45) ← NEXT
Confirm the 4-split scorecard for V42 = V41 + forward_pe_max=45.
Run against all splits and verify no OOS TPs are lost.
Check which Sep-Dec TPs are lost (expected: LRCX, HWM at pe >45).
Script should be very short — just run `_row4` for V42 on all splits and list lost TPs.

### 2. earnings_growth_min sweep
`earnings_growth` shows the strongest OOS KS signal of the unexplored features (KS=0.245,
strengthening from 0.095). OOS TPs have median 79% YoY earnings growth vs FP median 29%.
Try earnings_growth_min ∈ {0.05, 0.10, 0.15, 0.20} on V41_BASE (or V42_BASE after confirmed).
Key question: does the gate block OOS TPs or only Sep-Dec FPs?

### 3. Reddit re-scrape for numerical fundamentals thresholds
Arctic Shift API: `/api/comments/search?author=GarbageTimePro&q=debt&limit=100`
Also search: "P/E", "earnings", "FCF", "profitable", "payout", "growth"
The QCOM post said "balance sheet check" with FCF/debt focus — look for actual numbers in comments.
r/mLabsTrading subreddit posts may also have criteria hints.

### 4. Blog "Standout Names" mining
Weekly public recap posts have "Standout Names" table (Setup Score + Contract Score + mIQ)
for stocks that cleared the screener but weren't traded. Only Jun 22 recap has this so far.
URL pattern: `blog.mlabstrading.com/posts/[slug]`
Scrape all 2026 weekly recaps to find more filter-pass rows.

### 5. Expected move gate
He said "strike outside of expected move". Computable from `best_iv × sqrt(DTE/252) × price`
for ~5-day DTE. May already be implicit in IV gates but worth verifying.

---

## Important Files

| File | Purpose |
|---|---|
| `src/algo_detective/analyze.py` | Gate application logic — `_apply_criteria()`, sector-specific handlers |
| `src/algo_detective/store.py` | DB connection, `get_all_features()`, `get_all_options()` |
| `src/algo_detective/session32.py` | V41 scorecard (canonical baseline) |
| `src/algo_detective/session33.py` | Market cap floor sweep |
| `src/algo_detective/session34.py` | Fundamentals KS re-analysis (this session) |
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
7. **V41_BASE precision numbers**: session32 baseline (sdTP=114, oosTP=14) is canonical. Later sessions show sdTP=133 due to DB refresh — OOS TP count (14) is stable.
8. **debt_to_equity units**: stored as yfinance percentage (×100). NVDA=6.55 means 6.55% = 0.066 ratio. Any sweep must use range 50–500, NOT 0.5–5.0.
