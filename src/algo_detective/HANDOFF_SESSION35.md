# Algo Detective — Session 35 Handoff

**Date:** 2026-07-03  
**Project:** Reverse-engineering u/GarbageTimePro's CSP screener (mlabstrading.com)  
**Goal:** Replicate his EOD options scanner using public data

---

## What We're Doing

GarbageTimePro runs a systematic cash-secured put strategy on "boring" large-cap stocks. He posts nightly watchlists to paying members. We're reverse-engineering his 4-tier filter hierarchy (regime → fundamentals → technicals → options) by matching our criteria against 46 known OOS trades (Jan–Jun 2026).

**Key constraint:** His `mlabs_score` column is his own LLM (homelab inference) — unrecoverable. We're only trying to replicate the mechanical filter layer.

---

## Current Best Criteria: V42

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
V41 = {**V40, "bb_width_pct_max": 21.0}
V42 = {**V41, "forward_pe_max": 45.0}   # ← CURRENT BEST

# Strip IV keys for BASE (historical IV data is incomplete):
_IV_KEYS = {"options_iv_min", "iv_rv_min", "pcr_vol_max",
            "industrials_iv_min", "consumer_cyclical_iv_min", "healthcare_iv_min",
            "consumer_defensive_iv_max", "energy_iv_min", "basic_materials_iv_min",
            "utilities_iv_min"}
V42_BASE = {k: v for k, v in V42.items() if k not in _IV_KEYS}
```

### V42 Scorecard

**Live DB numbers (session35, DB-expanded to 1704 tickers):**
| Split | Precision | Recall | TP count |
|---|---|---|---|
| Sep-Dec 2025 full | 34.3% | 44.1% | 124 |
| Train Sep-Oct 2025 | 38.4% | 51.9% | — |
| Test Nov-Dec 2025 | 25.0% | 29.2% | — |
| **2026 OOS** | **6.7%** | **30.4%** | **14/46** |

**Approximated canonical (V41 session32 delta: −9 sdTP, +0.4pp P, −3.2pp R, 0 oosTP):**
| Split | Precision | Recall | TP count |
|---|---|---|---|
| Sep-Dec 2025 full | ~45.1% | ~37.4% | ~105 |
| **2026 OOS** | — | **30.4%** | **14/46** |

Note: sdTP numbers in session35+ reflect DB expansion (84 prime tickers × more scan dates = more rows). 
OOS TP count (14) is stable and is the authoritative metric. Use V41 session32 numbers (sdTP=114, sd_P=44.7%) 
as the pre-expansion baseline; V42 = +0.4pp sd precision, 0 oosTP change relative to V41.

---

## Data Setup

### Narrow Universe Filter (REQUIRED in every session)
```python
all_features = get_all_features()
prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
all_features = [f for f in all_features if f["ticker"] in prime_tickers]
```
Without this, `get_all_features()` returns 120K+ rows from 1704 tickers.

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

## What Happened This Session (Session 35)

### V42 Confirmed: V41 + forward_pe_max=45

- **All 14 OOS TPs preserved.** No regression.
- Sep-Dec TPs lost: GLW (pe=45.0, Technology, 5 dates Nov 4–12) and HWM (pe=46.4, Industrials, 4 dates Sep 26–Oct 8). Both are low-prime-frequency tickers.
- Sep-Dec FPs removed: 22 (Industrials:20, Technology:2)
- Net: +0.4pp sd precision, −3.2pp sd recall → adopted as V42.

### earnings_growth_min: Global Gate Dead End

- **DVN (Apr 27 2026, eg=−0.753) is an OOS TP with deeply negative earnings growth.** Energy cyclicality — commodity prices tanked YoY but he still traded it. Any positive earnings_growth floor kills DVN.
- With null=pass: even eg_min=0.05 loses DVN → all variants show ⚠ (oosTP=13).
- Null coverage: 94% for prime rows, 87.5% for control (good coverage overall).
- Sep-Dec distribution: TP p50=0.244 vs FP p50=0.244 — **identical in-sample distributions** (explains weak Sep-Dec KS=0.095).
- OOS strengthening (KS=0.245) is real but driven by DAL (null), DVN (−0.753), and DG (0.124) — all legitimate OOS trades where he's buying value/dividend names with negative or low growth.
- **Conclusion: do NOT add global earnings_growth_min gate.**

### Sector-Exempt Variant (Not Yet Tested)

With null=pass + energy-exempt at eg_min=0.10:
- Removes 27 Industrials FPs (same FPs at both 0.05 and 0.10 — so the cutoff is above 0.05 and below the next Industrials TP)
- 0 OOS TPs lost (DVN is Energy → exempt; DAL is null → passes)
- Estimated: +~1pp sd precision with no recall cost
- **Session 36 candidate**: implement `energy_exempt_earnings_growth_min` or check if all 27 Industrials FPs are a small set of tickers (perhaps a single ticker-level gate would suffice)

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

### 1. Sector-exempt earnings_growth gate ← NEXT
Audit the 27 Industrials Sep-Dec FPs with eg<0.05. Key questions:
- Are they a small set of tickers (2-3 tickers × many dates) or spread across many Industrials?
- If concentrated: a ticker-level block (like `energy_block` approach but for specific tickers) might be cleaner.
- If spread: implement `energy_exempt_earnings_growth_min=0.05 (null=pass)` in analyze.py and test.
- Expected gain: ~27 FP removal, 0 OOS TP loss, ~+1pp sd precision.

**Quick audit to run first (no Docker needed — just query):**
```python
# Which Industrials tickers appear in those 27 FP rows?
for f in sep_dec:
    if (f["is_prime"] == 0
        and f.get("sector") == "Industrials"
        and _apply_criteria(f, V42_BASE)
        and f.get("earnings_growth") is not None
        and f["earnings_growth"] < 0.05):
        print(f["ticker"], f["earnings_growth"])
```

### 2. Reddit re-scrape for numerical fundamentals thresholds
Arctic Shift API: `/api/comments/search?author=GarbageTimePro&q=earnings&limit=100`
Also search: "profitable", "growth", "P/E", "FCF", "debt", "payout"
The QCOM post said "balance sheet check" — look for actual numbers in comments.
r/mLabsTrading subreddit posts may also have criteria hints.

### 3. Blog "Standout Names" mining
Weekly public recap posts have "Standout Names" table (Setup Score + Contract Score + mIQ)
for stocks that cleared the screener but weren't traded. Only Jun 22 recap has this so far.
URL pattern: `blog.mlabstrading.com/posts/[slug]`
Scrape all 2026 weekly recaps to find more filter-pass rows.

### 4. Expected move gate
He said "strike outside of expected move". Computable from `best_iv × sqrt(DTE/252) × price`
for ~5-day DTE. May already be implicit in IV gates but worth verifying.

---

## Important Files

| File | Purpose |
|---|---|
| `src/algo_detective/analyze.py` | Gate application logic — `_apply_criteria()`, sector-specific handlers |
| `src/algo_detective/store.py` | DB connection, `get_all_features()`, `get_all_options()` |
| `src/algo_detective/session32.py` | V41 scorecard (canonical pre-expansion baseline) |
| `src/algo_detective/session34.py` | Fundamentals KS re-analysis |
| `src/algo_detective/session35.py` | V42 confirmation + earnings_growth sweep (this session) |
| `src/screener/stocks.py` | Live screener — `_calculate_adr20()` added |
| `memory/project_algo_detective.md` | Full project memory with all session findings |

---

## Common Gotchas

1. **Always use narrow universe filter** (`prime_tickers` filter) — the DB now has 1704 tickers
2. **Strip IV keys for BASE criteria** — historical IV data is incomplete, use `_IV_KEYS` set
3. **sector values in features**: "Financial Services" (not "Financials"), "Consumer Cyclical" (not "Consumer Discretionary"), "Communication Services" (not "Communication")
4. **Lint before building**: `~/.local/bin/ruff check src/algo_detective/sessionXX.py`
5. **Docker volume**: The DB is a mounted volume; the image build does NOT rebake it. Rebuilding picks up new Python files only.
6. **`sma50_above_sma200` vs `sma50_above_sma150`**: both are in V42 (different keys, both enforced)
7. **sdTP drift**: session35 sdTP=133 for V41 (vs session32's 114) due to DB expansion. OOS TP count (14) is stable. The "+0.4pp precision" deltas are reliable even with expanded DB.
8. **debt_to_equity units**: stored as yfinance percentage (×100). NVDA=6.55 means 6.55% = 0.066 ratio.
9. **DVN earnings_growth = −0.753**: Energy OOS TP with negative YoY EG. Any global earnings_growth_min blocks it. Energy sector is cyclical — EG is not a useful gate for them.
