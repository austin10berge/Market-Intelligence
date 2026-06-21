# Session Handoff — Algo Detective

**Last updated:** 2026-06-21 (Session 13 — CC RSI + Tech RSI gates, v31 defined)  
**Resume at:** Session 14

---

## What We're Doing

Reverse-engineering a friend's trading scanner that emits "prime state" tickers — stocks it considers ideal candidates for selling cash-secured puts (CSPs). We have a CSV of 281 (date, ticker) observations from Sep–Dec 2025.

**Repo:** `/home/dev/workspace/Market-Intelligence`  
**Module:** `src/algo_detective/`  
**Data:** `data/detective/prime_tickers.csv` (281 rows, 74 unique tickers, 36 unique dates)  
**DB:** populated SQLite at `settings.db_path` — 60,144 rows in `detective_features`

---

## Current State

### Best criteria found — two tracks

#### Track A: SP500 universe — v26 (broadest coverage)

Use when running against the full SP500 universe. All `*_iv_min`/`*_iv_max` keys require `detective_options` to be populated.

```json
{
  "sma50_above_sma200": 1,
  "market_cap_b_min": 25,
  "price_vs_ema200_pct_min": 0,
  "price_vs_ema200_pct_max": 42,
  "pct_from_52wk_high_max": 18,
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
  "forward_pe_max": 50,
  "communication_services_market_cap_b_min": 50,
  "iv_rv_min": 0.9
}
```

**Result on SP500 universe:** Precision=**11.6%** | Recall=69.8% | TP=196 | FP=1,490

Technology (399 FPs after v26) and Financial Services (240) are the remaining walls. Both sectors have overlapping IV distributions with prime picks. ADR% and PCR (put/call ratio) are the next promising features but require new data pipeline work.

#### Track B: Narrow universe — v28 (highest precision, 74-ticker set only)

Restrict rows to the 74 tickers that ever appear in prime_tickers.csv, then apply v28. This eliminates structural FPs (tickers the scanner never selects) and focuses on: *which days is each prime ticker picked?*

v28 = v26 + three tighter gates based on the "prime days are calmer / lower-volume" pattern:

```json
{
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
  "iv_rv_min": 0.9
}
```

Note: `pct_from_52wk_high_max` tightened 18→12. Added `bb_width_pct_max=14.0` (creates BB band 4–14%) and `volume_ratio_max=1.10` (prime days have below-average volume). v28 strictly dominates v27 on all metrics.

**Result on narrow (74-ticker) universe:** Precision=**37.1%** | Recall=54.4% | TP=153 | FP=259

v29 = v28 + two FS-specific gates (Financial Services must be trending AND quiet):

```json
{
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
  "financials_volume_ratio_max": 0.90
}
```

**Result on narrow (74-ticker) universe:** Precision=**41.4%** | Recall=47.3% | TP=133 | FP=188

Intuition: FS prime picks require the stock to be in a directional trend (ADX≥20) AND during a quiet period (volume ratio≤0.90). The FS FP days are when the same stock has lower ADX (more choppy/sideways) or elevated volume (news-driven moves).

### Runner-up (v18b — recall focus, no sector gates)

```json
{
  "sma50_above_sma200": 1,
  "market_cap_b_min": 25,
  "price_vs_ema200_pct_min": 2,
  "price_vs_ema200_pct_max": 42,
  "pct_from_52wk_high_max": 25,
  "rv20_max": 0.55,
  "dividend_yield_max": 2.5,
  "options_iv_min": 0.20
}
```

**Result on SP500 universe:** Precision=6.2% | Recall=77.6% | TP=218 | FP=3,308

### Full progression

| Version | Universe | Precision | Recall | TP | FP | Notes |
|---------|----------|-----------|--------|----|----|-------|
| v13 | full (1,682 ctrl) | 2.7% | 70.1% | 197 | 7,202 | Starting point |
| v13 | SP500 (500 ctrl) | 4.0% | 71.2% | 200 | 4,837 | Universe fix |
| v17a | SP500 | 4.2% | 75.1% | 211 | 4,823 | Drop rev_growth, fix ema200% cap |
| v17b | SP500 | 3.6% | 83.3% | 234 | 6,263 | Looser caps for ANET-type stocks |
| v18 | SP500 | 6.5% | 71.5% | 201 | 2,908 | + options IV gate |
| v18b | SP500 | 6.2% | 77.6% | 218 | 3,308 | + options IV gate, looser caps |
| v19 | SP500 | 6.7% | 70.8% | 199 | 2,753 | + fin_mcap>=100 + tech_fcf>=0.01 |
| v21 | SP500 | 7.3% | 74.0% | 208 | 2,621 | + ema200=0 + ind_iv + cc_iv gates |
| v22 | SP500 | 7.6% | 74.0% | 208 | 2,530 | + healthcare_iv + sector backfill |
| v23 | SP500 | 8.5% | 72.6% | 204 | 2,185 | + RE_block+CD_iv_max+En+BM+Util gates |
| v24 | SP500 | 10.3% | 70.1% | 197 | 1,717 | + adx>=15 + bb_width>=4.0 |
| v25 | SP500 | 11.2% | 69.8% | 196 | 1,561 | + forward_pe_max=50 + comsvc_mcap_min=50 |
| v26 | SP500 | 11.6% | 69.8% | 196 | 1,490 | + iv_rv_min=0.9 |
| v26 | Narrow (74T) | 24.3% | 69.8% | 196 | 610 | same criteria, universe reduced |
| v27 | Narrow (74T) | 34.0% | 52.3% | 147 | 286 | + bb_width_pct_max=14 + macd<=0.5 + pct52wk_max=12 |
| v28 | Narrow (74T) | 37.1% | 54.4% | 153 | 259 | + bb_width_pct_max=14 + volume_ratio_max=1.10 + pct52wk_max=12 |
| **v29** | **Narrow (74T)** | **41.4%** | **47.3%** | **133** | **188** | + financials_adx_min=20 + financials_volume_ratio_max=0.90 |
| v30c | Narrow (74T) | 42.6% | 46.3% | 130 | 175 | + pcr_vol_max=2.0 |
| v30b | Narrow (74T) | 45.1% | 35.6% | 100 | 122 | + pcr_vol_max=2.0 + rsi_max=60 |
| **v30a** | **Narrow (74T)** | **49.6%** | **21.0%** | **59** | **60** | + pcr_vol_max=2.0 + rsi_max=52 |
| v31a | Narrow (74T) | 45.2% | 40.2% | 113 | 137 | v29 + cc_rsi_max=44 + tech_rsi_max=54 |
| v31b | Narrow (74T) | 44.2% | 44.8% | 126 | 159 | v29 + cc_rsi_max=52 + tech_rsi_max=58 |

---

## Session 11 Key Findings (PCR Pipeline)

### PCR pipeline built — `src/algo_detective/options_chain.py`

Two-mode pipeline for put/call ratio:

**Historical backfill (bars endpoint):**
- Enumerates all near-money puts (±18% of close) and calls (±12%), next 2 Friday expirations
- Batch-fetches Alpaca `/v1beta1/options/bars` in groups of 100 OCC symbols
- Aggregates put/call volume per ticker per date → `pcr_vol`
- OI not available from historical bars → `pcr_oi = NULL` for backfill rows
- **Alpaca retention limit**: ~7 months. Sep-Oct 2025 is permanently lost; Nov-Dec 2025 available as of Jun 2026

**Daily snapshot (snapshots endpoint):**
- `fetch_snapshot_pcr(tickers)` — calls `/v1beta1/options/snapshots` for the whitelist
- Returns both `pcr_vol` and `pcr_oi` (OI available from snapshots)
- Best put IV also captured → can replace/supplement `options_build.py` going forward

**Store changes:** `detective_options` table extended with `pcr_vol REAL, pcr_oi REAL`. Schema migration is idempotent (ALTER TABLE loop in `ensure_tables()`). UPSERT uses COALESCE to preserve existing pcr values when updating just IV.

**Run backfill:**
```bash
docker compose build pipeline
docker compose run --rm pipeline python -m src.algo_detective.options_chain --backfill
# prime tickers only (default) — much faster than --all-tickers
```

**Run nightly snapshot:**
```bash
docker compose run --rm pipeline python -m src.algo_detective.options_chain --snapshot
```

### Reddit research note

The friend's Reddit account `u/GarbageTimePro` was attempted but is not machine-accessible:
- Reddit blocks WebFetch (HTTP 403/redirect)  
- No Playwright MCP is available in this environment (CLAUDE.md lists it for frontend testing but the server was not active)
- Google search returned no indexed posts for that username

**Must be read manually** by the user. Could provide additional insight into the exact scanner criteria, option strike selection logic, or PCR thresholds the friend uses.

### Next: add pcr_vol as a feature in narrow-universe analysis (Session 12)

Once backfill runs, create `session11.py` (or `session12.py`) that:
1. Joins `detective_options.pcr_vol` into the narrow-universe feature rows
2. Runs KS analysis: pcr_vol on prime days vs non-prime days for same tickers
3. Tests `pcr_vol_min` and `pcr_vol_max` gates (hypothesis: prime days may have LOWER pcr_vol — calls dominate, market is complacent, good time to sell puts)
4. Combine best pcr gate with v29 criteria

---

## Session 13 Key Findings (CC RSI + Tech RSI gates, v31)

### New keys in `_apply_criteria`

- `consumer_cyclical_rsi_max` — RSI ceiling for Consumer Cyclical rows (NULL passes)
- `technology_rsi_max` — RSI ceiling for Technology rows (NULL passes)

### Consumer Cyclical deep-dive

CC universe within v29 survivors is tiny: 7 TP / 13 FP. Tickers: AMZN x15 (7 TP, 8 FP), TJX x3, EBAY x2. It's essentially the question: on which days does the scanner pick AMZN?

**Surprise:** pct_from_52wk_high is inverted for CC (TP_med=10.1% vs FP_med=7.1%, KS=0.571) — AMZN prime days are *farther* from the 52wk high. price_vs_ema200_pct is the strongest discriminator (TP_med=2.2% vs FP_med=9.3%, KS=0.703) — AMZN is picked when barely above EMA200, not during a high-momentum run.

RSI sweep for CC: `cc_rsi_max=44` → cuts 4 FPs, loses 0 TPs (+0.7pp). Weak alone because it's only 20 rows total.

### Technology RSI

Tech TP tickers: NVDA (x10), GLW (x5), MSFT (x4), AAPL (x4), ADI (x3), NTAP (x3). Top FP tickers: ADI (x15), AAPL (x14), MSFT (x13), IBM (x9), NTAP (x7), APH (x7) — same stocks appear as both TP and FP on different days.

Tech feature distributions (TPs vs FPs, v29 survivors):
- rsi: TP_med=52.9 vs FP_med=54.3, KS=0.228
- price_vs_ema200_pct: TP_med=17.0% vs FP_med=11.7%, KS=0.274 — TPs are farther above EMA200 (NVDA drag)
- rv20: TP_med=0.287 vs FP_med=0.259, KS=0.244 (inverted — TPs have higher vol, NVDA effect)
- market_cap_b: TP_med=1,656B vs FP_med=234B, KS=0.202 — mega-cap TPs vs mid-cap FPs

`technology_rsi_max=54` → P=44.1%, R=41.6%, +2.7pp over v29 (cuts 31 FPs, loses 16 TPs).

### Combined v31

| Version | P | R | TP | FP | Notes |
|---------|---|---|----|----|----|
| **v31a** | **45.2%** | **40.2%** | **113** | **137** | v29 + cc_rsi_max=44 + tech_rsi_max=54 |
| v31b | 44.2% | 44.8% | 126 | 159 | v29 + cc_rsi_max=52 + tech_rsi_max=58 |

v31a strictly dominates all recall≥40% options. Tech RSI gate provides most of the gain; CC gate adds ~1pp at minimal recall cost.

### Ceiling analysis

After v31a at P=45.2%, R=40.2%, the remaining 137 FPs and 168 missed primes face the same structural challenge: same tickers appearing as both TP and FP on different days. The distinguishing signal on those border days is increasingly likely to be:
1. Intraday IV/spread data (scanner runs intraday, we have EOD)
2. mlabs_score (proprietary, unrecoverable)
3. Options chain characteristics at time of scan (delta, OI, spread)

### Reddit: u/GarbageTimePro — not machine-accessible

Reddit blocks WebFetch (HTTP 403). WebSearch returns no indexed posts for this username. Must be accessed manually in a browser. Could provide insight into exact scanner logic.

---

## Session 12 Key Findings (PCR + RSI gates, v30)

### PCR backfill — all 36 dates recovered

Contrary to the 7-month Alpaca retention assumption, **Sep-Oct 2025 data is still available**. All 36 dates were filled with 64-74/74 tickers per date. 95.4% of prime rows have pcr_vol.

Tickers missing pcr_vol: NFLX (5 rows), ATI (3), FLR/TOL/APH/DB/INCY (1 each) — illiquid options or data gaps.

### PCR signal — real but weak, right direction

On the full narrow universe: prime pcr_vol median=0.510 vs control=0.581 (KS=0.091, p=0.036). Within v29 survivors: TP median=0.454 vs FP median=0.507 (KS=0.136, p=0.10).

**Direction:** prime days have LOWER pcr_vol (fewer puts relative to calls) — the market is more bullish/complacent on days the scanner selects CSP candidates. This makes intuitive sense: if puts are cheap (low pcr_vol), IV premiums exist but hedging demand isn't panicked → ideal CSP conditions.

**Exception — Industrials: TPs have HIGHER pcr_vol (0.919 vs 0.805)**. The scanner picks industrials during periods of elevated put buying. Inverted pattern from the rest of the sectors.

pcr_vol_max gate sweep (on v29 base):

| Gate | P | R | TP | FP | ΔP |
|------|---|---|----|----|----|
| pcr_vol_max=0.60 | 45.7% | 32.0% | 90 | 107 | +4.3pp |
| pcr_vol_max=0.80 | 43.7% | 35.6% | 100 | 129 | +2.2pp |
| pcr_vol_max=1.00 | 42.7% | 37.7% | 106 | 142 | +1.3pp |
| pcr_vol_max=1.50 | 42.6% | 44.8% | 126 | 170 | +1.1pp |
| pcr_vol_max=2.00 | 42.6% | 46.3% | 130 | 175 | +1.2pp |

PCR alone is a weak gate — high precision requires losing too much recall.

### RSI is the strongest new signal

RSI sector breakdown within v29 survivors:

| Sector | TP median RSI | FP median RSI | KS |
|--------|-------------|-------------|-----|
| Financial Services | 51.5 | 61.1 | 0.355 |
| Consumer Cyclical | 46.1 | 51.5 | 0.462 |
| Technology | 52.9 | 54.3 | 0.228 |
| Communication Services | **48.8** | **45.9** | 0.266 (inverted!) |
| Industrials | 56.9 | 57.2 | 0.222 |

**ComSvc is inverted** — TPs have HIGHER RSI than FPs. Don't apply a global rsi_max for ComSvc rows.

Global RSI gate (v29 base):

| rsi_max | P | R | TP | FP | ΔP |
|---------|---|---|----|----|----|
| 52 | 46.5% | 21.0% | 59 | 68 | +5.0pp |
| 55 | 44.2% | 26.0% | 73 | 92 | +2.8pp |
| 57 | 44.4% | 29.9% | 84 | 105 | +3.0pp |
| 60 | 44.0% | 36.6% | 103 | 131 | +2.6pp |

Sector-specific `financials_rsi_max` is only +0.8pp at rsi_max=60 — the v29 ADX+volume gate already captures much of the same FS signal.

### v30 — pcr_vol_max + rsi_max combined

Two v30 variants:

**v30a (high precision, low recall):**
```json
v29 + {"pcr_vol_max": 2.0, "rsi_max": 52}
```
P=**49.6%** | R=21.0% | TP=59 | FP=60 | (+8.1pp over v29)

**v30b (balanced):**
```json
v29 + {"pcr_vol_max": 2.0, "rsi_max": 60}
```
P=**45.1%** | R=35.6% | TP=100 | FP=122 | (+3.6pp over v29)

**v30c (pcr only, maximum recall):**
```json
v29 + {"pcr_vol_max": 2.0}
```
P=42.6% | R=46.3% | TP=130 | FP=175 | (+1.2pp over v29)

The v29 + rsi_max alone at 60 gives P=44.0%, R=36.6% — slightly less than v30b but avoids requiring PCR data.

### Precision progression (narrow universe)

| Version | P | R | TP | FP | Notes |
|---------|---|---|----|----|----|
| v29 | 41.4% | 47.3% | 133 | 188 | FS ADX+vr gates |
| v30c | 42.6% | 46.3% | 130 | 175 | + pcr_vol_max=2.0 |
| v30b | 45.1% | 35.6% | 100 | 122 | + pcr_vol_max=2.0 + rsi_max=60 |
| v30a | **49.6%** | 21.0% | 59 | 60 | + pcr_vol_max=2.0 + rsi_max=52 |

### Hitting the ceiling

Precision at ~50% with recall at 21% is probably near the ceiling for technical + options features on this dataset. Remaining FPs are structurally hard to eliminate — they share the same tickers and similar indicator values as TPs, just on different days. The remaining discriminating signal likely comes from:

1. **Intraday options data**: The scanner runs intraday. Our EOD IV/PCR is noisier than what the friend sees
2. **mlabs_score**: Proprietary Market Rebellion Labs score in the CSV — unrecoverable
3. **More data**: Only 281 prime observations across 36 dates. More dates would allow better pattern detection

---

## Session 11 Key Findings (PCR Pipeline)

### PCR pipeline built — `src/algo_detective/options_chain.py`

Two-mode pipeline for put/call ratio:

**Historical backfill (bars endpoint):**
- Enumerates all near-money puts (±18% of close) and calls (±12%), next 2 Friday expirations
- Batch-fetches Alpaca `/v1beta1/options/bars` in groups of 100 OCC symbols
- Aggregates put/call volume per ticker per date → `pcr_vol`
- OI not available from historical bars → `pcr_oi = NULL` for backfill rows
- **Alpaca retention limit**: ~7 months. Sep-Oct 2025 is permanently lost; Nov-Dec 2025 available as of Jun 2026

**Daily snapshot (snapshots endpoint):**
- `fetch_snapshot_pcr(tickers)` — calls `/v1beta1/options/snapshots` for the whitelist
- Returns both `pcr_vol` and `pcr_oi` (OI available from snapshots)
- Best put IV also captured → can replace/supplement `options_build.py` going forward

**Store changes:** `detective_options` table extended with `pcr_vol REAL, pcr_oi REAL`. Schema migration is idempotent (ALTER TABLE loop in `ensure_tables()`). UPSERT uses COALESCE to preserve existing pcr values when updating just IV.

**Run backfill:**
```bash
docker compose build pipeline
docker compose run --rm pipeline python -m src.algo_detective.options_chain --backfill
# prime tickers only (default) — much faster than --all-tickers
```

**Run nightly snapshot:**
```bash
docker compose run --rm pipeline python -m src.algo_detective.options_chain --snapshot
```

### Reddit research note

The friend's Reddit account `u/GarbageTimePro` was attempted but is not machine-accessible:
- Reddit blocks WebFetch (HTTP 403/redirect)  
- No Playwright MCP is available in this environment (CLAUDE.md lists it for frontend testing but the server was not active)
- Google search returned no indexed posts for that username

**Must be read manually** by the user. Could provide additional insight into the exact scanner criteria, option strike selection logic, or PCR thresholds the friend uses.

---

## Session 10 Key Findings (Sector FP Deep-dive, ADX Gate, v29)

### Financial Services — temporal overlap + ADX is the key discriminator

Within v28 passes: 47 FS TPs, 97 FS FPs. The same tickers appear as both TPs and FPs on different dates (GS: 9 TP / 11 FP, BAC: 8/10, JPM: 8/12, WFC: 7/10, AXP: 5/18, MS: 4/19) — the scanner picks these stocks on some days and not others.

Best FS discriminators (TPs vs FPs, KS):

| Feature | KS | TP median | FP median | Direction |
|---------|-----|-----------|-----------|-----------|
| adx | 0.308 | 22.6 | 19.5 | TP>FP |
| rv20 | 0.294 | 0.200 | 0.219 | TP<FP |
| macd_histogram | 0.275 | -0.199 | +0.061 | TP<FP |
| rsi | 0.262 | 54.1 | 57.5 | TP<FP |
| volume_ratio | 0.256 | 0.790 | 0.860 | TP<FP |

FS prime days: trending (high ADX), quiet (low volume ratio, low rv20), flat-to-falling momentum (negative MACD histogram, lower RSI). FS FP days: untrendy, higher volume, bullish momentum.

Best FS-specific gates (on top of v28):
- `financials_adx_min=22`: P=**41.0%**, R=47.0%, TP=132, FP=190 (+3.8pp)
- `financials_adx_min=20`: P=40.2%, R=49.1%, TP=138, FP=205 (+3.1pp)
- `financials_volume_ratio_max=0.80`: P=40.7%, R=47.3%, TP=133, FP=194 (+3.5pp)
- `financials_volume_ratio_max=0.90`: P=39.1%, R=50.9%, TP=143, FP=223 (+1.9pp)

### Technology — inverted pattern (TPs have HIGHER volatility)

Within v28: 36 Tech TPs, 79 Tech FPs. Unlike the broad pattern, Tech TPs have HIGHER volatility than Tech FPs:

| Feature | KS | TP median | FP median | Direction |
|---------|-----|-----------|-----------|-----------|
| atr_pct | 0.305 | 2.79 | 2.38 | TP>FP (inverted!) |
| rv20 | 0.244 | 0.287 | 0.259 | TP>FP (inverted!) |
| market_cap_b | 0.202 | $1,656B | $234B | TP>FP |

Tech TPs are dominated by NVDA ($1.7T+) and AAPL/MSFT mega-caps. Tech FPs are mid-cap tech (ADI, IBM, NTAP, APH, NXPI). The scanner picks high-volatility mega-caps in tech, but calmer stocks in other sectors.

`technology_market_cap_b_min=250`: P=38.9%, R=48.8%, TP=137, FP=215 (+1.8pp over v28)

### v29 definition

**v29 = v28 + `financials_adx_min=20 + financials_volume_ratio_max=0.90`**: P=41.4%, R=47.3%, TP=133, FP=188

Full FS-specific ADX × volume_ratio grid (on top of v28):

| fs_adx_min | fs_vr_max | P | R | TP | FP |
|-----------|-----------|---|---|----|----|
| 15 | — | 37.1% | 54.4% | 153 | 259 |
| 17 | 0.90 | 39.8% | 49.8% | 140 | 212 |
| 20 | 0.90 | **41.4%** | **47.3%** | **133** | **188** |
| 22 | 0.90 | 42.0% | 45.9% | 129 | 178 |
| 25 | 0.90 | 41.0% | 42.4% | 119 | 171 |

Diminishing returns above adx=20/vr=0.90.

### Keys now implemented in `_apply_criteria`

- `financials_volume_ratio_max`: max volume_ratio for Financial Services
- `financials_adx_min`: min ADX for Financial Services (NULL fails)
- `technology_volume_ratio_max`: max volume_ratio for Technology
- `technology_market_cap_b_min`: min market_cap_b for Technology

### Remaining FP breakdown after v29 (~188 FPs)

- Technology ~60-70: ADI (temporal overlap — 15 FPs), AAPL (~14), MSFT (~13), IBM (~9), NTAP, APH
- Financial Services ~45-50: remaining FS days that pass adx≥20 + vr≤0.90 but aren't prime
- Industrials ~25-30: HWM, GE, ETN on non-prime days

Tech is now the biggest FP wall. Tech TPs have *higher* volatility than Tech FPs (inverted pattern) — the scanner picks NVDA/AAPL/MSFT during volatile phases but smaller tech FPs during calmer phases. `technology_market_cap_b_min=250` would cut many tech FPs but also loses GLW, ADI, NTAP TPs (recall → 41.3%).

---

## Session 09 Key Findings (FN/FP Profiling + v28)

### What was blocking the 134 v27 false negatives

v27's 3 new/tightened gates each block distinct sets of prime days:

| Gate | FNs blocked | Exclusively blamed |
|------|------------|-------------------|
| `pct_from_52wk_high_max=12` | 54 | 32 |
| `macd_histogram_max=0.5` | 40 | 24 |
| `bb_width_pct_max=14.0` | 34 | 15 |

Feature distributions of v27 FNs vs TPs:
- **bb_width_pct**: FN median=11.3 vs TP median=9.2 — FNs are in wider-BB (more volatile) phases
- **macd_histogram**: FN median=+0.03 vs TP median=-0.35 — FNs are in slightly bullish momentum
- **pct_from_52wk_high**: FN median=9.0% vs TP median=5.3% — FNs farther from highs

Top tickers with most missed primes: UAL (11), ANET (8), NVDA (7), XYZ/GS/NFLX/NEE (5 each). Many are chronic misses from previous sessions (ANET, NFLX, NEE) due to structural characteristics (high rv20, below EMA200, etc.).

### v27 FP profile (286 FPs)

| Sector | FPs | % |
|--------|-----|---|
| Financial Services | 109 | 38.1% |
| Technology | 83 | 29.0% |
| Industrials | 41 | 14.3% |
| Communication Services | 21 | 7.3% |

Top FP tickers: MS (22), ADI (19), BAC (17), WFC (15), AXP (15), HWM (14), GE (12), MSFT (11), ETN (11), IBM (11).

### volume_ratio is the new breakthrough gate

Within v27 survivors, TPs have lower volume than FPs: TP volume_ratio median=0.837 vs FP=0.942 (KS=0.212). Prime days have **below-average volume** — the scanner picks stocks during quiet consolidation, not high-volume events.

`volume_ratio_max=1.10` sweep (on top of v26 alone):
- vr=1.20: P=28.4%, R=65.5%, TP=184, FP=463 (+4.1% vs v26)
- vr=1.10: P=29.1%, R=61.2%, TP=172, FP=418 (+4.8%)
- vr=1.00: P=29.3%, R=51.6%, TP=145, FP=349 (+5.0%)

**With `bb_width_pct_max=14 + pct_from_52wk_high_max=12 + volume_ratio_max`:**

| vr_max | Precision | Recall | TP | FP |
|--------|-----------|--------|----|----|
| 0.85 | **41.3%** | 30.6% | 86 | 122 |
| 0.90 | **39.5%** | 36.3% | 102 | 156 |
| 0.95 | **38.7%** | 40.9% | 115 | 182 |
| 1.00 | **37.2%** | 45.9% | 129 | 218 |
| **1.10** | **37.1%** | **54.4%** | **153** | **259** |

v28 (`vr≤1.10`) at P=37.1% / R=54.4% strictly dominates v27 (P=34.0% / R=52.3%) — higher precision, higher recall, fewer FPs.

### Pareto frontier (narrow universe)

| Recall target | Best P | TP | FP | Gates (on top of v26) |
|---------------|--------|----|----|----------------------|
| ≥50% | 37.1% | 153 | 259 | bb≤14 + h52≤12 + vr≤1.10 (v28) |
| ≥55% | 32.6% | 155 | 320 | bb≤14 + macd≤0.5 + h52≤16 |
| ≥60% | 30.7% | 169 | 381 | bb≤14 + h52≤12 |
| ≥65% | 28.8% | 183 | 453 | bb≤14 (just BB band) |

### Remaining FP patterns after v28

- Financial Services (MS, BAC, WFC, AXP) is now the dominant FP sector. These pass all gates because they have large mcap, decent IV, and pass all volatility filters — the discrimination left is subtle.
- ADX signal within v27 survivors: TPs have higher ADX (22.2 vs 20.1, KS=0.194). But `adx_min` tightening kills recall sharply — adx_min=20 cuts to R=44.8%.
- Next opportunity: sector-specific `financials_volume_ratio_min` or `financials_adx_min` gate.

---

## Session 08 Key Findings (Narrow Universe — Gate Combinations + ML)

### The narrow-universe pivot

Restricting to the 74 prime tickers eliminates all structural FPs (stocks the scanner will never pick). The problem becomes: **for each of these 74 stocks, which days is it selected?** This is a much more tractable 10.5%-positive classification problem (vs 1.6% on SP500).

**Narrow universe stats:** 281 prime, 2,383 control, 2,664 total rows. 74 unique prime tickers, 36 dates.

### Key pattern: prime days have lower/calmer signals

KS analysis within v26 survivors (prime vs same-ticker non-prime days):

| Feature | KS | Prime median | Ctrl median | Direction |
|---------|-----|-------------|-------------|-----------|
| rv20 | 0.228 | 0.235 | 0.264 | prime < ctrl |
| macd_histogram | 0.217 | -0.236 | +0.044 | prime < ctrl |
| roc20 | 0.215 | 1.4% | 3.5% | prime < ctrl |
| bb_pct_b | 0.210 | 0.559 | 0.682 | prime < ctrl |
| bb_width_pct | 0.198 | 9.3 | 10.2 | prime < ctrl |
| rsi | 0.193 | 53.6 | 57.2 | prime < ctrl |
| volume_ratio | 0.192 | 0.853 | 0.953 | prime < ctrl |

Prime days are characterized by: lower momentum (roc20, macd negative), lower volatility (rv20, bb_width), lower RSI, lower relative volume. The scanner picks these stocks during calm, consolidating regimes — not during breakouts.

### Gate combinations — best results on narrow universe (all built on top of v26)

Two-gate best (recall≥50%):

| Gates | Precision | Recall | TP | FP |
|-------|-----------|--------|----|----|
| bb_width_pct_max=14 + macd_histogram_max=0.5 | 31.6% | 56.6% | 159 | 344 |
| bb_width_pct_max=14 + pct_from_52wk_high_max=12 | 30.7% | 60.1% | 169 | 381 |
| bb_width_pct_max=12 + pct_from_52wk_high_max=12 | 31.1% | 53.7% | 151 | 335 |
| rv20_max=0.25 (single gate) | 32.3% | 44.1% | 124 | 260 |

Three-gate best (recall≥50%):

| Gates | Precision | Recall | TP | FP |
|-------|-----------|--------|----|----|
| **bb_width<=14 + macd<=0.5 + pct52wk_max=12** | **34.0%** | **52.3%** | **147** | **286** |
| bb_width<=14 + macd<=0.5 + pct52wk_max=14 | 33.0% | 54.1% | 152 | 308 |
| bb_width<=14 + macd<=0.5 + adr20_pct_max=3.5 | 32.1% | 54.4% | 153 | 323 |

### ML on narrow universe — rules outperform on holdout

GBM (5-fold CV, stratified): AUC-ROC=0.848, AP=0.434. At recall≥70%: P=33.7% (+9.4% vs v26 baseline). Impressive CV numbers.

BUT — date-based holdout (train=24 dates, test=12):
- GBM holdout: AUC-ROC=0.712, P=16.2% at R=62.6%, FP=268
- v26 rules on same test set: P=18.8%, R=47.0%, FP=168

**v26 rules beat ML on honest holdout.** The narrow universe has only ~83 test prime rows across 12 dates — too small for GBM to generalize. CV AUC-ROC gap (0.848 CV vs 0.712 holdout) signals overfitting.

Feature importances on full narrow set (GBM): adx #1 (0.134), bb_width_pct #2 (0.116), best_iv_is_null #3 (0.090 — missing options data is itself a signal!), rv20 #4 (0.053), volume_ratio #5 (0.050). The `best_iv_is_null` flag being top-3 suggests: rows where we couldn't fetch options data are less likely to be prime picks.

### Conclusion

- **v27 (rule-based on narrow universe) is the current best**: P=34.0%, R=52.3%, TP=147, FP=286
- ML doesn't add value until we have more training data (more dates)
- Remain rule-based; focus on improving recall without losing precision gains
- Next opportunity: PCR (put/call ratio) pipeline — would add a new orthogonal signal

---

## Session 07 Key Findings (Sector FP Analysis, TS-CV, IV/RV Gate)

### Time-series-aware CV confirms GBM is genuinely useful (not just leaky)

Forward-chain (expanding-window) CV — train on dates 1..N, test on dates N+1..N+k:

| Fold | Train dates | Test dates | Test range | AUC | AP |
|------|------------|-----------|-----------|-----|-----|
| 1 | 12 | 4 | Oct 8 → Oct 15 | 0.838 | 0.268 |
| 2 | 16 | 4 | Oct 16 → Oct 23 | 0.953 | 0.421 |
| 3 | 20 | 4 | Oct 28 → Nov 4 | 0.929 | 0.187 |
| 4 | 24 | 4 | Nov 5 → Nov 12 | 0.929 | 0.209 |
| 5 | 28 | 8 | Nov 13 → Dec 8 | 0.908 | 0.143 |
| **mean** | | | | **0.911** | **0.246** |

**At recall≥72.6%: P=10.2%, TP=141, FP=1,240** (vs leaky CV 25.6%, holdout 7.9%).

TS-CV AUC=0.897 is well below leaky CV (0.959), confirming that session 06's 25.6% was inflated. At recall≥70%, TS-CV gives P=10.9% — essentially matching v24 rules (10.3%). GBM offers marginal but real improvement over the rule set. Practical implication: the ML model's true forward advantage is modest; the priority should be adding new features (ADR%, PCR) rather than tuning the existing model.

### Technology FP analysis — forward_pe and fcf are the discriminators

Technology TPs: 52, FPs: 482 in v24. Top KS statistics (TP vs FP within tech sector):

| Feature | KS | TP median | FP median |
|---------|----|-----------|-----------|
| market_cap_b | 0.362 | 497B | 213B |
| fcf | 0.348 | 13.1B | 3.6B |
| debt_to_equity | 0.318 | 28 | 48 |
| peg_ratio | 0.312 | 1.04 | 1.51 |
| macd_histogram | 0.302 | -0.48 | -0.01 |
| beta | 0.297 | 1.41 | 1.24 |
| forward_pe | 0.263 | 17.5 | 24.4 |

**Actionable gate:** `forward_pe_max=50` — zero TP loss, cuts 111 FPs globally (PANW PE=69, CRWD PE=108, DDOG PE=77 all eliminated). P: 10.3% → 10.9%.

Tech FP ticker notes: IBM (x34), PANW (x32), ADSK (x31), ADI (x28), AAPL (x27), CSCO (x24), QCOM (x24) are the biggest FP sources. Many have high PE or low IV relative to primes. The tech TP tickers are NVDA (x16), GLW (x6), AAPL (x5), ADI (x4), MSFT (x4).

No sector-specific tech mcap gate helps much — tech TPs span from $25B (ZM) to $5.1T (NVDA), too wide to floor.

### Communication Services analysis — mcap floor solves it

ComSvc TPs: 21 appearances from just 4 tickers: **BIDU, DIS, GOOG, META**. FPs: 155 in v24.

Top KS features (ComSvc TP vs FP): forward_pe (0.584), pct_from_52wk_high (0.493), beta (0.469), market_cap_b (0.417). **TP median mcap = 1,464B; FP median mcap = 180B.**

The 54 FPs in $25-50B range (TTWO x33 at $44.6B, TKO x12, LYV x9) dominate. Gate: `communication_services_market_cap_b_min=50` cuts 54 FPs, loses only BIDU (x1 at $37.8B). Implemented in `_apply_criteria`.

### IV/RV20 ratio — free win

Prime picks have higher IV-to-realized-vol ratio than FPs (TP p10=1.09 vs FP p10=0.99). At `iv_rv_min=0.9`: zero TP loss, 71 FPs cut. This makes sense — the CSP scanner requires IV premium over realized vol; rows with IV/RV < 0.9 have cheap options not worth selling.

Implemented in `_apply_criteria` as `iv_rv_min` key: fails if `best_iv is None` or `rv20 is None` or `best_iv/rv20 < val`.

### New parameters tested (user-requested)

| Parameter | Status | Result |
|-----------|--------|--------|
| Beta | In DB, already in ML | KS=0.297 within tech; no useful standalone rule (TP/FP distributions too similar globally) |
| IV/RV20 | Derived from best_iv/rv20 | iv_rv_min=0.9 → **free 71 FP cut, now in v26** |
| ATR% | `atr_pct` already in ML | Distributions nearly identical (same as session 06 finding) |
| ADR% | **Not in DB — needs pipeline** | See next steps below |
| PCR OI | **Not in DB — needs new Alpaca endpoint** | See next steps below |
| PCR Vol | **Not in DB — needs new Alpaca endpoint** | See next steps below |

### LightGBM vs GBM

| Metric | GBM (session06) | LightGBM (session07) |
|--------|----------------|----------------------|
| CV AUC-ROC | 0.959 | 0.957 |
| CV AP | 0.392 | **0.420** |
| CV P@R≥72.6% | 25.6% | **29.3%** |
| Holdout AUC-ROC | **0.910** | 0.887 |
| Holdout P@R≥70% | **7.9%** | 6.5% |
| Holdout TP | 59 | 60 |
| Holdout FP | **686** | 857 |

**GBM wins on the honest holdout.** LightGBM is slightly better in (leaky) CV but worse on unseen dates — likely overfitting given the small dataset (18K rows, only 36 unique dates). Recommendation: keep GBM for deployment.

LightGBM feature importances (by split count) differ from GBM (gain-based). No single feature dominates; top 5 are: `best_iv`, `adx`, `bb_width_pct`, `volume_ratio`, `rv20`. The `volume_ratio` signal (#4) suggests stocks with normal/quiet volume are preferred over high-volume events — but testing as a standalone rule shows modest gain (2 TP loss, 66 FP cut at max=2.0).

### ADR% (Average Daily Range) — feature added, backfill complete, not useful as rule

`adr20_pct` = 20-day mean of (high-low)/close × 100. Added to `features.py`, `detective_features` schema, and backfilled for all 60,144 rows via `backfill_adr.py`.

KS=0.075 — distributions nearly identical (TP median=2.23%, FP median=2.33%). No useful standalone threshold exists. Similar to ATR%, ADR% is another realized-range volatility measure highly correlated with existing features. May still provide additive signal in ML context since LightGBM uses features non-linearly.

Next step: add `adr20_pct` to `NUMERIC_FEATS` in session06.py (or session08.py) and retrain to check feature importance.

---

## Session 06 Key Findings (ML Classifier + ADX/BB Width)

### ML classifier results

**Stratified 5-fold CV (optimistic — time-series leakage):**

| Model | AUC-ROC | AP | Precision @72.6% recall |
|-------|---------|-----|------------------------|
| GradientBoosting | 0.959 | 0.392 | 25.6% (205 TP, 597 FP) |
| RandomForest | 0.949 | 0.284 | 20.5% (205 TP, 795 FP) |
| LogisticRegression | 0.889 | 0.110 | 7.9% (205 TP, 2394 FP) |

**Date-based holdout (honest — train Sep-Oct, test Nov-Dec):**
- GBM holdout: P=7.9%, R=71.1%, TP=59, FP=686
- v23 rules on same test set: P=6.6%, R=53.0%, TP=44, FP=625

### ML is marginally better than rules on unseen data

The 25.6% CV number is inflated by time-series leakage — random folds expose the model to the same ticker's patterns across time. The true forward performance (holdout) is ~7.9% vs rules at 6.6%. Only a 1.3pp improvement.

The low holdout recall for rules (53%) vs full dataset recall (72.6%) is because the test dates (last 12) include some prime tickers whose options expired 6-7 months ago — near Alpaca's retention boundary — causing more null IVs.

### Feature importances revealed two missed rule-based signals

GBM full-set feature importances (top 10):

| Rank | Feature | Importance | Notes |
|------|---------|------------|-------|
| 1 | market_cap_b | 0.301 | Already in v24 (min=25B) |
| 2 | atr_pct | 0.079 | No rule effect — distributions identical |
| 3 | sma50_above_sma200 | 0.065 | Already required |
| 4 | best_iv | 0.055 | Already in v24 (global + sector gates) |
| 5 | adx | 0.053 | **→ Added as adx_min=15 in v24** |
| 6 | price_vs_sma150_pct | 0.052 | Overlaps with ema200 feature |
| 7 | bb_width_pct | 0.038 | **→ Added as bb_width_pct_min=4.0 in v24** |
| 8 | beta | 0.037 | — |
| 9 | forward_pe | 0.031 | — |
| 10 | best_iv_is_null | 0.028 | Null indicator is a signal in itself |

`atr_pct` is #2 but cuts zero FPs as a standalone rule (prime/control distributions nearly identical). The ML uses it in non-linear combination with other features — a simple threshold on it is useless.

### ADX gate analysis

ADX prime: p25=18.0, median=21.5 vs control: p25=16.7, median=21.4. Slight separation at the lower tail.

At `adx_min=15`: -444 FPs, -7 TPs. 7 lost primes: FSLR x2 (ADX=15.0), MSFT (14.6), IBKR (15.0), DIS (13.6), CEG (14.6), SCHW (14.1) — all in low-trend periods for those specific dates.

### BB Width gate

Prime bb_width_pct: p10=5.78, p25=7.39 vs control: p25=6.61. At `bb_width_pct_min=4.0`: -24 FPs, -0 TPs. Free.

### For proper ML evaluation, use time-series-aware CV

The next step if pursuing ML is **TimeSeriesSplit** or forward-chain CV (train on dates 1-N, test on N+1...N+k), repeating over expanding windows. This avoids the leakage that inflates the 25.6% number to something realistic.

---

## Session 05 Key Findings (Sector-Specific IV Gates + Sector Backfill)

### Sector backfill: 12 "Unknown" tickers had NULL sector in universe_fundamentals

All 12 were well-known SP500 stocks (AAPL, CAT, C, CBOE, CDNS, CCL, CAH, CEG, CASY, CBRE, A, BSX). After assigning correct sectors, existing gates automatically eliminated:
- **CBOE** (33 FPs) → Financial Services, mcap=$26B < $100B → eliminated by `financials_market_cap_b_min`
- **CAT** (27 FPs) → Industrials, IV=0.253 < 0.30 → eliminated by `industrials_iv_min`

Fix applied directly via SQL UPDATE to both `universe_fundamentals.sector` and `detective_features.sector`. Free 60 FP reduction.

### Sector-specific IV floors are highly effective

Adding per-sector IV minimums cuts FPs with zero or minimal TP loss because prime picks in each sector tend to have higher IV than FP stocks in the same sector:

| Sector gate | FPs cut | TPs lost | Net |
|-------------|---------|----------|-----|
| sector backfill (CBOE+CAT fix) | 60 | 0 | Free data fix |
| `industrials_iv_min: 0.30` | 186 | 0 | Pure win |
| `consumer_cyclical_iv_min: 0.30` | 113 | 1 | ~Win |
| `healthcare_iv_min: 0.25` | 42 | 0 | Pure win |
| `financials_market_cap_b_min: 100` | 150 | 2 | Win (NU collateral) |
| `real_estate_block: 1` | 34 | 0 | Pure win (0 RE primes) |
| `consumer_defensive_iv_max: 0.32` | 90 | 1 | Win (FP div-stocks have higher IV than prime div-stocks) |
| `energy_iv_min: 0.38` | 114 | 3 | Win |
| `basic_materials_iv_min: 0.38` | 69 | 0 | Pure win |
| `utilities_iv_min: 0.50` | 38 | 0 | Pure win |
| `technology_fcf_min: 0.01` | 5 | 0 | Negligible (SP500 all profitable) |

**Consumer Defensive inverse:** FP Consumer Defensive stocks have *higher* IV (0.360) than prime picks (0.255). This makes sense — the scanner selects stable, low-volatility defensives (WMT, PG types), not high-IV defensive names. Hence `consumer_defensive_iv_max` instead of `_min`.

**Communication Services (182 FPs):** IV distributions are almost identical (prime mean=0.422, FP mean=0.414). No IV gate helps here. This is the remaining wall along with Technology (597 FPs).

### EMA200% floor: lower to 0% (not remove entirely)

`price_vs_ema200_pct_min: 2` was excluding stocks in short-term pullbacks (INTU, DIS, AMZN at 0.3–1.6% above EMA200). Setting to `0` instead of removing the key is crucial — removing the key entirely lets in stocks *below* EMA200 (+416 extra FPs).

With `min: 0` and the sector IV gates combined: +10 TPs, slight FP increase offset by sector gates → net improvement.

### Technology FP wall remains

Technology is now the biggest FP sector (541). Prime tech IV median=0.465 vs FP=0.404 — distributions overlap too much for a simple IV gate without significant recall loss. A higher `technology_iv_min` trades recall for precision unfavorably. SP500 tech is dominated by profitable mega-caps (AAPL, MSFT, NVDA, GOOGL) that all look like legitimate primes.

### FCF gate for Technology is useless on SP500

`technology_fcf_min: 0.01` cuts only 5 FPs because all SP500 tech stocks are already profitable (positive FCF). Useful in a broader universe but not here.

### NFLX null IV — data retention, not a bug

NFLX options data is null for Sep–Oct 2025 (prime dates). The options expired ~8-9 months ago, outside Alpaca's ~7-month retention window. Dec 2025 NFLX data comes through fine. These 5 TPs are permanently unrecoverable from this data source.

### Collateral misses from sector gates

- **PHM x2**: market_cap=$24.4B, just below $25B global floor. A structural near-miss.
- **NU x2**: NU Holdings (fintech) is $62.74B — fails `financials_market_cap_b_min: 100`. NU is not in SP500. Collateral from the financials gate that was worth it for the 150 FP reduction.

### Current miss breakdown (v22, 73 missed primes = 26%)

| Ticker | Count | Root cause |
|--------|-------|-----------|
| ANET | 8 | rv20 > 0.45 (high-momentum tech) |
| XYZ | 5 | pct_from_52wk_high 19-26% |
| NFLX | 5 | Alpaca data retention (Sep-Oct expired) |
| NEE | 5 | dividend_yield 2.89% > 2.5% cap |
| PINS | 3 | below EMA200, 52wk > 18% |
| NKE | 3 | below EMA200, 52wk > 18%, div 3.64% |
| ATI | 3 | null IV (illiquid options or data) |
| PHM | 2 | mcap $24.4B < $25B floor |
| INTU | 2 | ema200% -0.18 and -4.96% (in pullback) |
| NU | 2 | financials mcap gate ($62.74B < $100B) |
| UAL | 2 | 52wk > 18%, rv20 ≈ 0.45 |
| GILD | 2 | dividend 2.58% > 2.5% cap |

---

## Session 04 Key Findings

### Universe: SP500 is correct, not nyse_large|nasdaq_large

`nyse_large ∪ nasdaq_large` in the DB = 2,155 tickers ≈ the full 2,244-ticker control universe. Filtering on it gives no improvement. The real scanner universe is the **S&P 500 (~500 tickers)**:

- Control tickers in SP500: 500 of 1,682
- Prime tickers in SP500: 60/74 (81.1%)  
- 14 prime tickers outside SP500: AAL, ATI, BABA, BIDU, DB, DKS, EMBJ, FLR, NU, PINS, TME, TOL, WPM, ZM

To filter in code:
```python
conn = _get_connection()
sp500 = set(r[0] for r in conn.execute(
    "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
).fetchall())
conn.close()
filtered = [f for f in features if f['is_prime'] == 1 or f['ticker'] in sp500]
```

### revenue_growth_min was wrong — drop it

ATI (revenue_growth=0.006) and DHR (0.037) both appear in the prime list. The scanner does NOT filter on revenue growth. Removing it recovers these misses without meaningful precision loss when combined with a higher market_cap floor.

### price_vs_ema200_pct_max should be ~42%, not 35%

GLW (Corning) was 36–40% above EMA200 across 5 prime dates in Nov 2025 — passed every other criterion but failed the 35% cap. Raising to 42% captures GLW with minimal FP cost (+75 FPs vs +7 TPs).

### IV floor ≥ 20% — a critical scanner constraint we can't replicate

CSV `iv` column is in **percentage form** (e.g., 38 = 38% annualized IV). Stats across all 281 prime picks:

| Stat | iv (%) | annual_yield_pct (%) | pop_pct (%) | delta | cushion_pct (%) |
|------|--------|---------------------|-------------|-------|-----------------|
| min | 20 | 20 | 70 | -0.30 | 1 |
| p25 | 31 | 28 | 76 | -0.29 | 2 |
| median | 37 | 44 | 78 | -0.27 | 3 |
| p75 | 47 | 83 | 81 | -0.24 | 4 |
| max | 86 | 516 | 93 | -0.20 | 12 |

**Hard constraints visible in data:**
- IV ≥ 20% on every single pick (floor, possibly 15% or 20% scanner threshold)
- Delta always -0.20 to -0.30 (25-30 delta puts)
- PoP ≥ 70% always (follows from delta)
- True IV/RV ratio: median ≈ 1.09 (IV slightly above RV — scanner doesn't require IV >> RV)

We cannot apply the IV ≥ 20% filter to control stocks without options data. `rv20_min` as a proxy doesn't help much (too many FP SP500 stocks also have high rv20).

### mlabs_score — proprietary scoring present in data

The CSV `mlabs_score` column ranges 39.4–78.2 (median 60.2). Likely a Market Rebellion Labs proprietary score that does final ranking/filtering. We have no way to replicate this.

### Persistent misses — root causes now identified

| Ticker | Dates missed | Root cause |
|--------|-------------|-----------|
| ANET | 8 | rv20 > 0.45 on most dates AND/OR ema200% > 35% (high-momentum stock) |
| NEE | 5 | dividend_yield = 2.89% > our 2.5% cap consistently |
| XYZ | 5 | Likely data quality artifact |
| ATI | 5 | revenue_growth = 0.006 < 0.05 (wrong filter — drop rev_growth) |
| GLW | 5 | price_vs_ema200_pct 36-40% > our 35% cap (use 42% cap) |
| INTU | 4 | Oct 2025 pullback: pct_from_52wk_high > 18% or rv20 spike |
| DIS | 4 | Oct 2025 pullback: pct_from_52wk_high > 18% |
| NKE | 3 | MULTIPLE failures: below EMA200, >18% off high, div 3.64%, rev_growth ~0% |
| DHR | 3 | revenue_growth = 0.037 < 0.05 (drop rev_growth) |

v17a's remaining 70 misses are dominated by ANET (8), NEE (5), and pullback-period tickers.

### Sector-stratified KS analysis

**Financial Services** (58 prime, 9,015 control):
- `market_cap_b` is overwhelmingly dominant (KS=0.861) — prime financials avg $370B vs $47B control
- This means the scanner selects megabanks (JPM, GS, BAC, C) and excludes regional banks
- `pct_from_52wk_high` is #3 (prime avg 4.9% vs 12.4%) — near-high requirement is sector-consistent

**Technology** (71 prime, 8,980 control):
- `market_cap_b` still #1 (KS=0.645) — prime tech avg $1,462B vs $97B (AAPL/MSFT/NVDA vs speculative tech)
- `fcf` is #2 (KS=0.564) — prime tech avg $15B FCF vs $0 — massive FCF discriminates mega-cap tech
- `rv20` is #10 (KS=0.309) — prime tech has lower RV (0.340) vs control (0.467)

### ANET — captured with rv20<=0.60, ema200%<=42, 52wk<=25

All 9 ANET prime dates pass with those wider caps. Testing this on SP500:
- `v17b` already uses rv20<=0.55, ema<=42, 52wk<=25 → captures 8/9 ANET dates

---

## How to Run Things

```bash
cd /home/dev/workspace/Market-Intelligence

# Validate criteria (stock criteria only)
docker compose run --rm pipeline python -m src.algo_detective.validate \
  --criteria '{"sma50_above_sma200": 1, "market_cap_b_min": 25, ...}'

# Build options IV data from Alpaca (run once; skip_existing=True by default)
docker compose run --rm pipeline python -m src.algo_detective.options_build
docker compose run --rm pipeline python -m src.algo_detective.options_build --all  # recompute all

# Validate with options gate (from Python — CLI doesn't wire options yet)
# features already joined in validate_criteria when 'options_iv_min' is in criteria

# Re-run KS analysis
docker compose run --rm pipeline python -m src.algo_detective.analyze

# Build feature matrix (skip already-computed pairs)
docker compose run --rm pipeline python -m src.algo_detective.build

# Session 04 experiments (all 5 experiments)
docker compose run --rm pipeline python -m src.algo_detective.session04

# Run tests
docker compose run --rm test python3 -m pytest tests/test_algo_detective_*.py -v
```

**Important:** After editing any `.py` file, rebuild the pipeline image before running:
```bash
docker compose build pipeline
```

**To run validate on SP500 universe (from Python):**
```python
from src.algo_detective.store import _get_connection, get_all_features
from src.algo_detective.validate import validate_criteria, print_report
features = get_all_features()
conn = _get_connection()
sp500 = set(r[0] for r in conn.execute(
    "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
).fetchall())
conn.close()
filtered = [f for f in features if f['is_prime'] == 1 or f['ticker'] in sp500]
report = validate_criteria(your_criteria, features=filtered)
print_report(report)
```

---

## Options Gate — Key Facts

The `detective_options` table holds one row per (date, ticker) with `best_iv` = the highest implied volatility found across ±1 strike and 2 Friday expirations from each scan date. Built by `options_build.py` using Alpaca historical bars + Black-Scholes IV back-calculation.

**Coverage:** 59.5% of rows have IV data (10,732/18,023). Nulls come from:
1. Stocks where our estimated strike didn't trade (most common — illiquidity signal)
2. Tickers with hyphens (`BF-B`, `BRK-B`) — URL encoding bug; always null

**Null = fails `options_iv_min`** (the `_min` semantics in `_apply_criteria`). This is correct behavior for most nulls (genuine illiquidity) but wrong for hyphen tickers (data artifact). BF-B/BRK-B are not prime picks, so the bug doesn't affect recall.

**IV distribution:**
- Prime rows: p25=0.29, median=0.39, p75=0.53
- SP500 control: p25=0.26, median=0.36, p75=0.50

The distributions nearly overlap — Sep-Dec 2025 was a high-volatility period. In calmer markets, the IV gate would be much more discriminating.

**FP breakdown at v18 threshold (IV≥0.20):**
- 33.7% of FPs eliminated because null (options illiquid)
- 6.0% eliminated because IV < 20%
- 60.3% of FPs pass IV≥0.20 — these are the hard-to-eliminate FPs (large-cap momentum stocks that genuinely have elevated IV)

**DTE insight (from the CSV):** The scanner targets weekly options — median DTE = 7, most are DTE 1-10. It always picks the nearest upcoming Friday expiration. Weekly options require liquid options markets; this is already proxied by market_cap_b but an options liquidity filter (volume/OI) would be more precise.

**Known limitations of the IV estimate:**
- Uses rv20 to compute target strike → circular relationship with IV
- Closing price (EOD) vs. scanner's intraday pricing → IV error of ±5-10pp
- 400 errors on batches containing hyphen tickers (BF-B, BRK-B) → permanently null

## Code Structure

| File | Purpose |
|------|---------|
| `src/algo_detective/store.py` | SQLite DDL + CRUD. `ensure_tables()`, `get_all_features()`, `backfill_fundamentals()` |
| `src/algo_detective/features.py` | `compute_features(ticker, date, df, sector)` → 50+ indicators |
| `src/algo_detective/universe.py` | `get_control_tickers(date, exclude)` and batch OHLCV load |
| `src/algo_detective/build.py` | CLI orchestrator. `--backfill-fundamentals` flag. |
| `src/algo_detective/analyze.py` | KS ranking (`rank_features`), threshold search (`find_thresholds`), `_apply_criteria` |
| `src/algo_detective/validate.py` | `validate_criteria(criteria, features=None)` → precision/recall/FP-by-sector/missed-primes. CLI defaults to SP500 universe. |
| `src/algo_detective/ingest.py` | CSV parser → `PrimeTicker` dataclass |
| `src/algo_detective/session04.py` | Session 04 experiments (universe restriction, ANET, sector KS, IV analysis, grid search) |
| `src/algo_detective/session05.py` | Session 05 experiments (sector mcap/FCF/IV gates, EMA200 floor, v22 construction) |
| `src/algo_detective/options_build.py` | Fetches historical options IV from Alpaca, stores in `detective_options` |
| `src/algo_detective/session07_tech_fp.py` | Technology FP analysis (KS stats, gate candidates, ticker breakdown) |
| `src/algo_detective/session07_comsvc.py` | Communication Services FP analysis |
| `src/algo_detective/session07_ts_cv.py` | Forward-chain time-series CV for GBM (honest ML evaluation) |
| `src/algo_detective/session07_lgbm.py` | LightGBM comparison experiment |
| `src/algo_detective/backfill_adr.py` | One-time backfill: computes adr20_pct for all existing detective_features rows |
| `src/algo_detective/session08.py` | Narrow universe pivot: KS analysis, single/two/three-gate sweeps, ML (GBM). Establishes "prime days are calmer" pattern. |
| `src/algo_detective/session09.py` | FN attribution (which gate blocks which primes), volume_ratio_max discovery, v28 construction. |
| `src/algo_detective/session10.py` | Sector FP deep-dive: FS ADX gate, Tech inverted-volatility pattern, v29 construction. |
| `src/algo_detective/options_chain.py` | PCR pipeline: historical bars backfill + daily snapshot mode. Stores pcr_vol/pcr_oi in detective_options. |
| `src/algo_detective/session12.py` | PCR + RSI analysis on narrow universe. Joins pcr_vol from detective_options, sweeps pcr_vol gates and RSI gates, sector RSI breakdown, v30 definition. |
| `src/algo_detective/session13.py` | CC RSI + Tech RSI gate analysis. Deep-dives CC (AMZN-centric) and Tech (NVDA/mega-cap) sectors, sweeps `consumer_cyclical_rsi_max` and `technology_rsi_max`, defines v31. |

### _apply_criteria semantics (analyze.py)
- `_min` keys: NULL fails (we can't confirm floor is met)
- `_max` keys: NULL passes (unknown doesn't violate ceiling)
- `bool/int` keys: exact match required
- Sector-scoped special keys — NULL `best_iv` fails for that sector's rows:
  - `options_iv_min` — global IV floor
  - `financials_market_cap_b_min` — mcap floor for Financial Services only
  - `communication_services_market_cap_b_min` — mcap floor for Communication Services only
  - `technology_fcf_min` — FCF floor for Technology only
  - `industrials_iv_min` — IV floor for Industrials only
  - `consumer_cyclical_iv_min` — IV floor for Consumer Cyclical only
  - `technology_iv_min` — IV floor for Technology only
  - `healthcare_iv_min` — IV floor for Healthcare only
  - `energy_iv_min`, `basic_materials_iv_min`, `utilities_iv_min` — sector IV floors
  - `consumer_defensive_iv_max` — IV ceiling for Consumer Defensive (NULL passes)
  - `real_estate_block` — exclude all Real Estate rows
  - `iv_rv_min` — minimum IV/RV20 ratio; NULL best_iv or rv20 fails
  - `financials_rsi_max` — RSI ceiling for Financial Services (NULL passes)
  - `consumer_cyclical_rsi_max` — RSI ceiling for Consumer Cyclical (NULL passes)
  - `technology_rsi_max` — RSI ceiling for Technology (NULL passes)
  - `pcr_vol_max` / `pcr_vol_min` — global PCR volume ratio gates (requires `best_iv` join from detective_options in caller)

---

## Key Facts About the Data

- `sma50_above_sma200 = 1` on **100%** of prime rows — perfect requirement
- `dividend_yield`: 45.6% NULL rate in prime rows → use only as `_max` (NULL-tolerant)
- `fcf`: 20.3% NULL rate — same caution
- `earnings_growth`: 6% NULL rate — manageable
- `market_cap_b`, `beta`, `revenue_growth`: 0% nulls — safe to use as filters
- 7 prime tickers < $15B market cap: AAL, EMBJ, FLR, PINS, TME, TOL, WYNN
- **revenue_growth is NOT a scanner criterion** — ATI (0.6%) and DHR (3.7%) are in prime list

---

## Suggested Next Steps (priority order)

### 1. ✅ PCR pipeline + analysis complete (Sessions 11-12)

`options_chain.py` built, all 36 dates backfilled (incl. Sep-Oct 2025 — data still available). Analysis complete in `session12.py`.

Key results: pcr_vol_max is real but weak. RSI is stronger. Best combined v30a: P=49.6%, R=21.0%.

### 2. ✅ CC RSI + Tech RSI gates — Session 13 complete

`consumer_cyclical_rsi_max` and `technology_rsi_max` added to `_apply_criteria`. Full sweep done in `session13.py`.

Key results:
- **CC RSI gate is almost purely about AMZN**: the entire CC universe is AMZN x15 (7 TP + 8 FP), TJX x3, EBAY x2. `cc_rsi_max=44` cuts 4 FPs, loses 0 TPs. Weak alone (+0.7pp), powerful combined with Tech RSI.
- **Tech RSI**: `technology_rsi_max=54` is the sweet spot — +2.7pp, cuts 31 FPs, loses 16 TPs. Tech TPs are dominated by NVDA (x10), AAPL (x4), MSFT (x4). ADI (x15), AAPL (x14), MSFT (x13) are the top FP tickers — same stocks appear as TP and FP on different days.
- **Best combined v31**: `v29 + cc_rsi_max=44 + tech_rsi_max=54` → **P=45.2%, R=40.2%, TP=113, FP=137** (+3.8pp over v29). Best recall-preserving: `cc_rsi_max=52 + tech_rsi_max=58` → P=44.2%, R=44.8%.
- **Consumer Defensive RSI is inverted** (new finding): TP_med=57.8 vs FP_med=46.8 — a potential `consumer_defensive_rsi_min` gate, but only 4 FP rows total so low impact.

**v31 definition (narrow universe):**
```json
{
  ...v29...,
  "consumer_cyclical_rsi_max": 44,
  "technology_rsi_max": 54
}
```
P=45.2% | R=40.2% | TP=113 | FP=137

### 3. Technology FP wall — next angles (precision vs recall tradeoff)

### 3. Financial Services FP analysis (240 FPs remaining in v26)

The FS sector has 240 FPs at v26 (16% of total). The existing `financials_market_cap_b_min=100` already cuts small financials. The remaining 240 are large financials that look like prime candidates.

Quick test: what do FS FPs look like vs FS TPs?
```python
fs_tp = [f for f in v26_tp if f.get('sector') == 'Financial Services']
fs_fp = [f for f in v26_fp if f.get('sector') == 'Financial Services']
```
Check IV, adx, forward_pe distributions. The scanner may prefer financials with high IV (banks, brokers during volatile periods).

### 4. Retrain ML model with adr20_pct added

`adr20_pct` is now in the DB. Add to `NUMERIC_FEATS` in session06.py (or write session08_ml.py):
```python
NUMERIC_FEATS = [...existing..., "adr20_pct"]
```
Then retrain GBM with the same TS-CV approach from session07_ts_cv.py. Check whether adr20_pct appears in feature importances — KS=0.075 as a rule but might be useful in combination.

### 5. NEE recovery tradeoff

NEE (div_yield=2.89%) is missed x5. Raising `dividend_yield_max` from 2.5 to 3.0 adds +8 TPs and +259 FPs — unfavorable precision-wise. Not worth it unless recall is the priority.

### 6. ML with new features

Once ADR% and/or PCR are added to the DB:
- Add to `NUMERIC_FEATS` in session06.py (or new session08.py)
- IV/RV20 ratio can already be computed at build time: `iv_rv_ratio = best_iv / rv20` as a pre-computed column (more stable than computing in the criteria engine)
- Retrain GBM/LGB with expanded feature set, check new importances

---

## Session Notes

- `data/detective/sessions/session-01.md` — initial KS results, first criteria explorations
- `data/detective/sessions/session-02.md` — added fundamentals, market_cap as dominant signal
- `data/detective/sessions/session-03.md` — NULL-tolerant _max fix, precision/recall frontier, v13 as best balanced criteria
- Session 04 experiments: `src/algo_detective/session04.py`
- Session 05 experiments: `src/algo_detective/session05.py`
- Session 06 experiments: `src/algo_detective/session06.py` (ML classifier)

---

## Open Question: What the Scanner Is Doing We Can't Model

**Rule-based ceiling is ~7.5% precision.** The scanner likely emits 5-15 stocks per day from 500, implying its true precision is 1-3%. We're now ~2.5–7.5x away from that, meaning we're catching ~208/281 prime picks but with ~2,579 FPs that would never be picked by the scanner.

The remaining gap is likely explained by:
1. **mlabs_score threshold**: A proprietary Market Rebellion Labs score (ranges 39.4–78.2 in CSV, median 60.2). This final filter is unrecoverable without the actual scanner and is likely responsible for most of the remaining FP gap.
2. **Intraday options criteria**: The scanner runs during market hours. Our IV estimates use EOD prices (±5-10pp error). Exact delta, PoP, spread width, and OI checks at quote time would further narrow candidates.
3. **Technology sector is the wall**: 541 Technology FPs remain, and Technology prime IV (median=0.465) barely separates from FP IV (median=0.404) at the SP500 level — these are all high-quality large-cap tech stocks.
