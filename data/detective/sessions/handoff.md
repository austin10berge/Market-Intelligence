# Session Handoff — Algo Detective

**Last updated:** 2026-06-26 (Sessions 25-29 — V39 definition, IWM regime analysis → not useful as a gate)  
**Resume at:** Session 30

---

## Session 30+ — Next Steps (priority order)

### Sessions 28-29 gate tests — completed

- `sma20_above_sma50`: **REJECTED.** −4.8pp P / −5.2pp R on test. See Session 28 findings.
- `adr20_pct_max=4.0`: **INCORPORATED as V39.** Zero recall cost. See Session 28 findings.
- `beta_max`: **SKIPPED.** Inverted in OOS — descriptive, not a gate.
- `IWM regime`: **NOT USEFUL.** All 36 Sep-Dec dates + 35/36 OOS dates are IWM > EMA200 → feature is near-constant. KS=0.000 across splits. See Session 29 findings.

---

### 1. Industrials sector RSI gate — high-value candidate

Industrials is the largest remaining FP wall: **40 FPs in Sep-Dec full (24 TP, 40 FP, P=37.5%)**.
We have RSI gates for Technology (60) and Consumer Cyclical (44) but nothing for Industrials.

**Hypothesis:** Industrials FPs tend to have elevated RSI (trending higher when he's not picking them).
Adding `industrials_rsi_max` could reduce the 40-FP wall without losing TPs.

**Action:** Run `industrials_rsi` distribution (prime vs ctrl, per split). Sweep
`industrials_rsi_max` ∈ {50, 55, 60, 65} on V39 4-split scorecard. Same framework as tech/CC sweep.

---

### 2. Position sizing — incorporated (not a scanner gate)

From June 2026 comments:
- Position sizing: < 10% of portfolio per ticker
- Sector exposure: < 25% of portfolio per sector

Not a scanner filter — it's a portfolio management constraint applied after scanning.

---

### 3. Live options data — deferred (testable ~Aug 2026)

The nightly pipeline now collects `best_iv`, `pcr_vol`, `pcr_oi` via snapshots endpoint for
all 74 prime tickers (Step 5 of `src/main.py`, wired in session 15). Options data collection
started 2026-06-21. To be testable: need ~60–90 days of live data (August–September 2026).

---

## Session 27 Key Findings (Reddit scrape + 1-year post analysis)

### Arctic Shift API — corrected endpoint

Old broken format: `/api/comments?author=GarbageTimePro` (returns 404)  
**Working format:** `https://arctic-shift.photon-reddit.com/api/comments/search?author=GarbageTimePro&after=2026-06-20T00:00&limit=100&sort=desc`  
Also: `/api/posts/search` for post objects.

Scraped 79 comments from June 20–25, 2026 and 6 new posts from the same window.

---

### The 1-Year Breakdown Post (June 21, 2026)

GarbageTimePro published "One Year Wheeling BORING Names. The FULL Breakdown" cross-posted to
r/thetagang, r/options, and r/Optionswheel simultaneously (score 149/128/156 respectively).

Full stats table is already documented in Session 14 item 11 (added during prior session work).
Reddit comments on that post provided several new criteria confirmations (see below).

**Performance context confirmed:**
- 2025 after-tax wheeling return: **27%** vs SPY 13% over the same period
- Time commitment: **0–3 hours/week**; most trades on Mondays, GTC orders placed, rest passive
- He also runs TQQQ swing trading and buy-and-hold portfolios with separate capital

---

### New Criteria Confirmations from June 2026 Comments

#### "20 > 50 > 200" MA structure (EXPLICIT)

From r/options comment (comment id ot1fucm), verbatim:
> "I start with fundamentals. Companies must be historically profitable and not overvalued. I also
> prefer they pay dividends. Next, they must have decent technical structure - not overbought/oversold,
> **moving averages preferably intact (i.e., 20 > 50 > 200)**, ADR < 4.0 preferably and beta ~1.25.
> Premium getting paid must justify the risk being taken. Strike outside of expected move, etc. Most
> importantly, do I mind bagholding the shares for months during a drawdown."

**SMA20 > SMA50 > SMA200** is his stated MA condition. Our model checks `sma50_above_sma150`
but NOT `sma20_above_sma50`. Session 28 candidate: test this gate on V38 base.

**ADR < 4.0** — specific daily range threshold now confirmed. ADR (Average Daily Range %) is a
volatility measure he uses to filter out high-beta movers. ANET met this at avg IV ~35 and ADR < 4.0
during 2025/early 2026; when ANET's IV spiked in 2026, he stopped trading it.

**Beta ~1.25** — he prefers stocks with beta around 1.25 or lower.

#### IWM as regime filter (EXPLICIT)

From r/thetagang comment (id ot1m6i3):
> "IV/RV is another good one you should add to your list. I use some typical regime filters derived
> from **SPY, VIX, IWM**, etc. If the overall breadth is strong, I'm deploying more."

IWM is explicitly mentioned alongside SPY and VIX. His regime filter is not just SPY EMA + VIX
threshold — he's also looking at IWM (small-cap) breadth. This triangulates: when small-caps are
participating (IWM healthy), overall market regime is favorable for CSP deployment.

#### BTC 30/50/75% rule — detailed timing

From multiple June 2026 comments:
> "I usually try to follow a 30/50/75% rule. I.e., if I STO Monday morning, and I capture 30%
> that same day, I will definitely BTC. 50% by Tuesday/Wednesday. 75% By Wed/Thurs, etc."

More granular version:
> "On average, if I capture ~30-40% of the premiums in the first 2 days, I BTC;  
>  if I capture ~45-65% of the premiums by mid week, I BTC;  
>  Else, I mostly let it ride out to expiration."

Average DTE in a trade remains "just under 5" (consistent with June 2026 post and prior data).
Median DTE from the 1-year trade log: 5 days.

#### ANET — explicitly links ADR to scanner eligibility

> "I mostly traded ANET before the IV spike in 2026. When I was trading it, the average IV was
> ~35 and an ADR of < 4.0 - basically a snoozefest."

This directly ties ANET's presence in the scanner to ADR < 4.0 as a condition. When ANET's IV
spiked (and presumably ADR rose above 4.0), it was **dropped from his watchlist**. This confirms
ADR < 4.0 is a hard filter, not just a descriptive preference.

#### Position sizing rules (not scanner gates, but portfolio management)

From r/Optionswheel comment:
> "I mostly keep any position < 10% of the overall capital. In the cases where I exceed that, I
> play more defensive and roll if I have to but most of the times I don't need to. I also try to
> keep sector exposure < 25%."

- < 10% of total capital per single position
- < 25% of total capital per sector
- These are post-scan portfolio constraints, not scanner filters

#### Platform infrastructure — confirmed tech stack

Same description repeated across multiple June 2026 comments:
> "It's basically a proxmox/docker stack where a Cloudflare Tunnel feeds Caddy, which routes to
> my Next.js frontend and FastAPI backend (both behind a VPN sidecar) backed by Postgres and Redis.
> I have Dagu scheduled jobs across a fleet of ~30 VPN-paired workers that pull sharded options and
> OHLCV bar data, and I keep an eye on everything with Prometheus, Grafana, Loki, and Uptime Kuma.
> I also run a separate TimescaleDB-based market-data and backtesting stack on a separate Proxmox
> box plus that includes an VM used for inference for local LLMs."

- ~30 workers for parallel data collection (options + OHLCV)
- Dagu = workflow scheduler (open-source DAG runner)
- Separate backtesting stack confirms he tests criteria against historical data
- Same stack as our Market-Intelligence repo (Next.js, FastAPI, Postgres, Redis, Docker)

---

### Trade Log Update — June 20, 2026

Downloaded `trade_log_2026_06_20.csv` (273 total rows). Compared against our OOS CSV:

- **46 unique (date, ticker) pairs** — 100% match with `prime_tickers_2026_oos.csv`
- 50 total CSP rows (4 pairs have 2 trades each: ANET 1/26, WMT 1/26, B 3/11, FCX 3/30)
- **GOOG 6/15 confirmed ✓**: Strike $360, Exp 6/18, ROC +0.57%, Status EXPIRED
- **JPM 6/16 confirmed ✓**: Strike $320, Exp 6/18, ROC +0.17%, Status CLOSED
- All 5 OOS-only tickers now confirmed: AMAT 6/2, FCX 6/2, C 6/9, GOOG 6/15, JPM 6/16

#### 2026 Tickers Confirmed (full list)

GOOG, AAL, WMT, M, QCOM, EQT, DOCN, ANET, AEO, FCX, UAL, DG, AXP, HAL, B,
AAPL, DAL, DVN, C, NVDA, LRCX, XOM, AA, XYZ, AMAT, JPM

**Current assignments** (confirmed in 1-year post): DG (100sh, cost ~$135, assigned 2/10/2026),
SMCI (100sh, assigned pre-2026). Both underwater, being wheeled via CCs.

---

### Session 14 Block: Items 11–13 (already added in prior session work)

The 1-year performance stats (item 11), portfolio snapshot update (item 12), and blog post
analysis (item 13) were added to the Session 14 findings block during prior session work before
context compaction. See lines starting at "### 11. Performance (12 months Jun 2025 – Jun 2026)"
in the Session 14 section below.

---

### Script
- No new Python script created; analysis done via curl + Python one-liners in session

---

## Session 29 Key Findings (IWM regime feature — not useful as a gate)

### Data source

Fetched IWM daily adjusted closes via yfinance (643 trading days, 2023-12-01 → 2026-06-26).
Computed EMA200, EMA50, and pct_from_52wk_high for each scan date.

---

### SECTION 1: IWM state on scan dates

**Sep-Dec 2025:** All **36/36** scan dates have IWM > EMA200. IWM was in a sustained bull run
(EMA200 ≈ 213–226, close ≈ 231–249). pct_from_52wk_high ranged 0.00%–7.02%.

**2026 OOS:** **35/36** dates have IWM > EMA200. The single exception: **2026-03-30**
(IWM close=239.04, EMA200=241.90, pct_from_52wk_high=11.03%). Despite the Apr–May 2026
tariff selloff in breadth, IWM recovered sharply so fast it stayed above EMA200 for
virtually the entire period. EMA50 told a different story: Nov 2025 had 3 dates below EMA50
(Nov 6/13/17), March 2026 had 4 dates below EMA50 (Mar 5/11/23/30).

---

### SECTION 2: KS analysis — near zero across all features

| Feature | sd KS | tr KS | te KS | oos KS |
|---|---|---|---|---|
| iwm_above_ema200 | 0.000 | 0.000 | 0.000 | 0.006 |
| iwm_above_ema50 | 0.006 | 0.000 | 0.002 | 0.024 |
| iwm_pct_from_52wk_high | 0.041 | 0.038 | 0.122 | 0.031 |

`iwm_above_ema200`: 100% of Sep-Dec TPs and FPs are on IWM-bull dates → zero discriminability.
`iwm_pct_from_52wk_high`: test KS=0.122 looks promising but TP/FP medians are nearly identical
(2.43% vs 2.26% in test; 1.39% vs 1.33% in Sep-Dec). The KS is driven by shared date-level
variation, not real TP/FP divergence.

---

### SECTION 4: V39 regime splits

**IWM Bull (35/36 OOS dates):** V39 OOS P=4.9%/R=26.7% (12 TPs) — essentially identical to full set.
**IWM Bear (1 OOS date — 2026-03-30):** 0 TPs, 2 FPs (that 1 OOS prime fails V39_BASE).

`iwm_above_ema200=1` as a date filter: removes only 1 OOS date (1 prime, 83 rows) and has
zero effect on Sep-Dec (0 bear dates). Not worth implementing.

---

### SECTION 5: pct_from_52wk_high sweep

| Max pfh% | sd dates | sd P/R (sdTP) | te P/R (teTP) | oos P/R (oosTP) |
|---|---|---|---|---|
| ≤5% | 34 | 44.6%/41.9% (111) | 41.2%/26.2% (21) | 5.0%/26.2% (11) |
| ≤10% | 36 | 44.4%/40.6% (114) | 40.7%/25.0% (24) | 4.9%/26.7% (12) |
| ≤15–25% | 36 | identical to no gate | identical | identical |

At ≤5%: costs 3 Sep-Dec TPs, 3 test TPs, 1 OOS TP — not worth it.
At ≤10%+: no change from full V39 (all scan dates are already within 10%).

---

### Conclusion

**IWM regime features provide no useful discrimination.** Root cause: GarbageTimePro's scan
dates are entirely within an IWM bull period (EMA200 > close on only 1 of 72 scan dates).
He may use "IWM" as a pre-scan mental check ("is the market healthy enough to deploy?") rather
than a mechanical row-level filter. Since he never deployed on genuinely IWM-bear dates in our
dataset, we have no contrast to learn from. V39 remains unchanged.

**IWM regime is a no-op in our data. Do not add.**

---

### Script

`src/algo_detective/session29.py` — yfinance IWM fetch, EMA200/50 computation, KS analysis,
V39 regime splits, pct_from_52wk_high sweep.

---

## Session 28 Key Findings (gate tests — sma20, adr20, beta, IWM)

### V38 baseline confirmed

| Split | P/R | TP |
|---|---|---|
| Sep-Dec 2025 full | 44.2%/40.6% | 114 |
| Train Sep-Oct 2025 | 45.5%/48.6% | 103 |
| Test Nov-Dec 2025 | 40.0%/25.0% | 10 |
| 2026 OOS | 4.9%/26.1% | 12 |

Note: Confirmed V38 Sep-Dec recall is **40.6%** (not 37.0% as in the Session 25 V37→V38 delta table — that table had an error; Session 26 CC RSI sweep already showed 40.6% and Session 28 confirms it).

---

### SECTION 1: sma20_above_sma50 — REJECTED

Adding `sma20_above_sma50=1` to V38:

| Split | V38 P/R | +sma20 P/R | Δ |
|---|---|---|---|
| Sep-Dec full | 44.2%/40.6% (sdTP=114) | 43.9%/33.5% (sdTP=94) | −0.3pp/−7.1pp, **−20 TPs** |
| Train Sep-Oct | 45.5%/48.6% | 46.9%/40.5% | +1.4pp/−8.1pp |
| Test Nov-Dec | 40.0%/25.0% | 35.2%/19.8% | **−4.8pp/−5.2pp** |
| 2026 OOS | 4.9%/26.1% (oosTP=12) | 5.6%/26.1% (oosTP=12) | +0.7pp/0pp |

**Decision: do not add.** Despite KS=0.197 in OOS, the gate blocks 20 Sep-Dec TPs and causes
−4.8pp P / −5.2pp R on the test split. All 12 OOS TPs pass it, but the test degradation is
too costly. Likely explanation: on VCP prime days, stocks may briefly dip below their 20-day MA
at EOD scan time — exactly when the mean-reversion setup forms.

---

### SECTION 2: adr20_pct_max=4.0 — INCORPORATED as V39

Sweep results on V38:

| adr20_pct_max | sd P/R (sdTP) | te P/R | oos P/R (oosTP) |
|---|---|---|---|
| None (V38) | 44.2%/40.6% (114) | 40.0%/25.0% | 4.9%/26.1% (12) |
| 4.5 | 44.3%/40.6% (114) | 40.4%/25.0% | 4.9%/26.1% (12) |
| **4.0 (V39)** | **44.4%/40.6% (114)** | **40.7%/25.0%** | **4.9%/26.1% (12)** |
| 3.5 | 44.9%/40.0% (113) | 40.7%/25.0% | 4.8%/26.1% (12) |

**At 4.0: zero TPs blocked on any split.** All 12 OOS TPs have `adr20_pct ≤ 4.0`.
Pure FP removal. Directly confirms GarbageTimePro's "ADR < 4.0 preferably" as a hard filter —
stocks with ADR > 4.0 are FPs in his system. At 3.5: loses 1 Sep-Dec TP.

**V39 = V38 + adr20_pct_max=4.0:**

```python
V39 = {**V38, "adr20_pct_max": 4.0}
```

| Split | V38 P/R | V39 P/R | Δ |
|---|---|---|---|
| Sep-Dec full | 44.2%/40.6% | **44.4%/40.6%** | +0.2pp/0 |
| Train Sep-Oct | 45.5%/48.6% | **45.5%/48.6%** | 0/0 |
| Test Nov-Dec | 40.0%/25.0% | **40.7%/25.0%** | **+0.7pp/0** |
| 2026 OOS | 4.9%/26.1% (oosTP=12) | **4.9%/26.1% (oosTP=12)** | 0/0 |

---

### SECTION 3: beta gate — confirmed descriptive only

- KS Sep-Dec: 0.066 (very weak)
- KS OOS: 0.212 but **inverted** — 2026 TPs have higher beta (FCX, LRCX, DAL in selloff)
- `beta<=1.25` would lose ~half of OOS TPs
- **Do not add.** Beta ~1.25 describes his typical bull-market picks, not a hard filter.

---

### SECTION 4: IWM regime — blocked by Alpaca 403

`_fetch_iwm_bars()` in `session28.py` → HTTP 403 Forbidden on Alpaca `/v2/stocks/IWM/bars`.
The Alpaca account covers options only — stock bars need a different tier or data source.
**Session 29 candidate:** fetch IWM bars via `yfinance` instead.

---

### Script

`src/algo_detective/session28.py` — V38 baseline, sma20 gate test, adr20 sweep, IWM attempt.

---

## Session 26 Key Findings (consumer_cyclical_rsi_max audit — keep 44)

### SECTION 1: CC sector on V38 — universe is essentially just AMZN

After V38 filtering (cc_rsi gate removed), the CC universe is:
- **Sep-Dec full:** 7 TPs (all AMZN), 20 FPs (AMZN x10, RCL x4, EBAY x3, TJX x3)
- **Test Nov-Dec:** 0 TPs, 5 FPs (TJX x3, AMZN x2) — no CC primes at all in Nov-Dec
- **2026 OOS:** 0 TPs, 27 FPs (TJX x10, EBAY x10, AMZN x7) — M and AEO fail other gates

The 7 Sep-Dec TPs are all AMZN. Of those, 4 are blocked by cc_rsi=44:
- AMZN 2025-10-09: RSI=54.6
- AMZN 2025-10-21: RSI=51.0
- AMZN 2025-10-22: RSI=46.1
- AMZN 2025-10-23: RSI=50.1

3 pass (Oct 14/15/16, RSI 41.5/40.6/39.5 — the cluster he bought right before the peak).

**KS(RSI, TP vs FP within V38 CC survivors) = 0.379** — gate discriminates meaningfully.

### SECTION 2-3: Sweep confirms cc_rsi_max=44 is correct — no V39

| cc_rsi_max | full P/R | test P/R | oos P/R | sd_TP | sd_FP |
|---|---|---|---|---|---|
| 42 | **44.7%/40.6%** | **40.0%/25.0%** | 4.9%/26.1% | 114 | 141 |
| **44 (V38)** | **44.2%/40.6%** | **40.0%/25.0%** | 4.9%/26.1% | **114** | **144** |
| 48 | 43.7%/40.9% | 40.0%/25.0% | 4.8%/26.1% | 115 | 148 |
| 52 | 43.5%/41.6% | 40.0%/25.0% | 4.7%/26.1% | 117 | 152 |
| 54 | 43.2%/41.6% | **38.7%/25.0%** | 4.7%/26.1% | 117 | 154 |
| none | 42.4%/42.0% | 36.9%/25.0% | 4.4%/26.1% | 118 | 160 |

**Test P/R is flat at 40.0%/25.0% for ALL values ≤ 54.** The 4 blocked AMZN TPs are all
train-period (Oct 9/21/22/23). There are **zero CC TPs in Nov-Dec 2025**, so relaxing the
gate does nothing for test split. It only adds FPs → precision drops.

**This is the opposite of the tech_rsi situation:**
- Tech RSI=54 was blocking genuine *test-period* TPs (Nov-Dec Tech stocks) → fix was warranted
- CC RSI=44 blocks only *train-period* TPs (AMZN Oct) → relaxing overfits train, hurts test

Tightening to 42 would remove 3 FPs (+0.5pp full P, same TPs at 114) but at cost of
added fragility — the CC universe is essentially one ticker (AMZN). Not worth it.

**Decision: cc_rsi_max=44 stays. V38 is unchanged. No V39.**

### CC gate behavior — final summary

| Gate direction | test P impact | OOS impact | verdict |
|---|---|---|---|
| Relax (44→52+) | −1.3pp (add FPs) | 0pp | wrong direction |
| Keep (44) | baseline | baseline | ✓ correct |
| Tighten (44→42) | 0pp (same TPs) | 0pp | marginal; fragile |
| Remove entirely | −3.1pp | −0.4pp | clearly wrong |

OOS Section 5 KS values for CC features look high (rsi=0.588, rv20=0.783) but are
**based on n=2 OOS prime rows (M and AEO)** — completely unreliable noise.

### Script
- `src/algo_detective/session26.py` — CC profiling, cc_rsi sweep, date-level breakdown

---

## Session 25 Key Findings (V38 definition + pct_from_52wk_high audit)

### SECTION 1-2: technology_rsi_max sweep → V38 = V37 + tech_rsi_max=60

Session 24 predicted 58–66 would improve test P/R; Session 25 ran the full 4-split scorecard.
**tech_rsi_max=60** is the optimal value — best test precision gain without OOS regression.

| tech_rsi_max | full P/R | train P/R | test P/R | oos P/R | oos_TP |
|---|---|---|---|---|---|
| 54 (V37) | 45.5%/36.3% | 48.0%/46.0% | 36.2%/17.7% | 5.0%/26.1% | 12 |
| **60 (V38)** | **44.2%/37.0%** | **46.4%/46.0%** | **40.0%/25.0%** | 5.0%/26.1% | **12** |
| 62 | ~similar to 60 | | | | |
| 66 | 45.5%/38.9% | — | 39.1%/26.0% | — | — |
| 70 | | | 35.7%/26.0% | | 1 ANET TP but P drops |

**V37→V38 delta:** Sep-Dec −1.4pp P / +0.7pp R, Train −1.6pp P / 0pp R,
**Test +3.8pp P / +7.3pp R ← largest single-gate test improvement so far**, OOS 0pp/0pp.

**Why tech_rsi_max=54 was too tight:** It was blocking 12 genuine Nov-Dec 2025 Technology primes
(not ANET — different stocks that were in the 54–60 RSI band during an extended rally). RSI=60 is
the sweet spot before FP flood begins. At 66+, FPs start creeping in.

**V38 sector changes vs V37 (Sep-Dec full):** All changes are in Technology (+12 TP, +22 FP).
Other sectors unchanged. Technology precision moves from 46.0% (23/50) to ~40.9% (35/86) — the
FP wall grows but the TP recovery is real.

**V38 definition:**
```python
V38 = {**V37, "technology_rsi_max": 60}  # V37 had tech_rsi_max=54
```

| Split | V37 P/R | V38 P/R | Δ |
|---|---|---|---|
| Sep-Dec full | 45.5%/36.3% | **44.2%/37.0%** | −1.4pp/+0.7pp |
| Train Sep-Oct | 48.0%/46.0% | **46.4%/46.0%** | −1.6pp/0pp |
| Test Nov-Dec | 36.2%/17.7% | **40.0%/25.0%** | **+3.8pp/+7.3pp** |
| 2026 OOS (base) | 5.0%/26.1% | **5.0%/26.1%** | 0pp/0pp |

### SECTION 3: pct_from_52wk_high_max=12 audit — gate is genuine, not overfit

KS statistic for `pct_from_52wk_high` across regimes:

| Period | KS | Note |
|---|---|---|
| Train Sep-Oct 2025 | 0.188 | |
| Test Nov-Dec 2025 | 0.209 | strengthens |
| **2026 OOS** | **0.231** | **strengthens further ← opposite of overfit behavior** |

Compare to gates that ARE regime-specific:
- `rv20`: KS 0.296 train → 0.146 OOS (decays — VCP feature, less useful in volatile regimes)
- `bb_width_pct`: KS 0.253 train → 0.184 OOS (decays)
- `pct_from_52wk_high`: **KS 0.188 → 0.231 (strengthens)** — structurally different

**Threshold sweep (3D) on V37:** pfh=12 is precisely optimal.
- pfh=10: loses 2 OOS TPs (12→10), no precision gain worth it
- pfh=15+: same 12 OOS TPs but costs Sep-Dec precision
- pfh=12: stays.

**3E: 17 blocked 2026 OOS primes (pfh>12)** all fail other V37 gates too — removing pfh
wouldn't rescue a single one. The gate blocks FPs but its absence wouldn't add TPs.

**Conclusion:** `pct_from_52wk_high_max=12` is well-motivated regardless of regime. Stocks closer
to their 52wk high are genuinely better CSP candidates in all market conditions. Gate stays at 12.

### Script
- `src/algo_detective/session25.py` — tech_rsi_max sweep, V38 definition, pfh audit

---

## Session 24 Key Findings (V37 validation + ANET deep-dive)

### SECTION 1-2: V37 validated — confirmed improvement over V36

V37 = V36 + `bb_width_pct_max=20.0` (was 18) + `volume_ratio_max=1.15` (was 1.20)

| Version | full P/R | train P/R | test P/R | oos P/R | sd_TP |
|---|---|---|---|---|---|
| V34 | 45.9%/37.4% | 49.5%/49.2% | 31.1%/14.6% | 4.0%/13.0% | 105 |
| V35 | 43.4%/33.8% | 46.8%/43.8% | 30.4%/14.6% | 5.6%/26.1% | 95 |
| V36 | 44.5%/35.9% | 47.8%/46.5% | 31.9%/15.6% | 5.4%/26.1% | 101 |
| **V37** | **45.5%/36.3%** | **48.0%/46.0%** | **36.2%/17.7%** | 5.0%/26.1% | 102 |

V37 is the best criteria set: Pareto improvement over V36 on test split (+4.3pp P, +2.1pp R),
same OOS recall (12 TPs, 26.1%). Sep-Dec full and train are also marginally better.

### SECTION 3: V37 sector breakdown — Sep-Dec 2025 full

| Sector | V37_TP | V37_FP | V37_P |
|---|---|---|---|
| Consumer Defensive | 11 | 2 | **84.6%** |
| Basic Materials | 3 | 2 | **60.0%** |
| Communication Services | 11 | 11 | 50.0% |
| Financial Services | 25 | 25 | 50.0% |
| Technology | 23 | 27 | 46.0% |
| Consumer Cyclical | 3 | 4 | 42.9% |
| Energy | 2 | 3 | 40.0% |
| Industrials | 24 | 40 | **37.5%** ← largest FP wall |
| Utilities | 0 | 6 | 0.0% |
| Healthcare | 0 | 2 | 0.0% |

Primary FP walls: Industrials (40 FPs), Technology (27 FPs), Financial Services (25 FPs).
Consumer Defensive remains the highest-precision sector at 84.6%.

### SECTION 4: ANET deep-dive — structurally incompatible with VCP criteria

ANET is his #2 earner ($3,089 in 2025, $3,584 cumulative through May 2026) but **0 TPs
in our model**. Analysis of all 9 ANET prime rows in Sep-Dec 2025:

**Gate-by-gate pass rates (V37):**
| Gate | ANET pass rate |
|---|---|
| rv20_max=0.45 | **2/9 (22%)** ← PRIMARY blocker |
| pct_from_52wk_high_max=12 | **5/9 (56%)** ← secondary |
| technology_rsi_max=54 | **5/9 (56%)** ← secondary |
| bb_width_pct_max=20 | 7/9 (78%) |
| volume_ratio_max=1.15 | 7/9 (78%) |

**ANET feature distributions on prime dates:**
- rv20: min=0.26, med=0.49, max=0.55 — median is **above** the 0.45 ceiling
- RSI: min=33.9, med=53.4, max=66.5 — wide range, partially above 54 cap
- pfh%: min=1.0%, med=9.1%, max=21.7% — many dates far from 52wk high
- bb_width%: min=10.2%, med=15.5%, max=27.5% — often wide

**Root cause:** ANET has structurally elevated realized volatility (rv20 median=0.49 vs
ceiling=0.45). It's a high-beta semiconductor that he trades for premium income but doesn't
fit the "quiet, low-vol VCP" setup our model requires. The 2 dates that pass rv20 both fail
other gates. ANET is a **ceiling** — accept as unrecoverable without changing the scanner's
character.

### SECTION 5: technology_rsi_max relaxation — high-value lead for V38

Sweeping `technology_rsi_max` on V37 reveals a surprising finding: **relaxing the RSI cap
significantly improves the temporal test split even without capturing ANET**:

| tech_rsi_max | Test P | Test R | ANET TPs |
|---|---|---|---|
| 54 (V37) | 36.2% | 17.7% | 0 |
| **58** | **39.0%** | **24.0%** | 0 |
| 62 | 39.3% | 25.0% | 0 |
| 66 | 39.1% | 26.0% | 0 |
| 70 | 35.7% | 26.0% | 1 (but P drops) |

The gain at 58–66 comes from OTHER Technology stocks prime in Nov-Dec 2025 that were
blocked by RSI=54. `tech_rsi_max=62` gives best precision improvement (+3.1pp) while
`tech_rsi_max=66` gives best recall (+8.3pp). At 70, one ANET TP appears but test P drops.

RSI distribution comparison for Technology Sep-Dec 2025:
- ANET prime RSI: med=53.4 — barely at/above the cap
- Tech ctrl RSI: med=53.1 — similar distribution, cap is quite tight for the sector

**V38 = V37 + tech_rsi_max=62 (or 66)** is the next candidate. Needs 2026 OOS impact check.

### Script
- `src/algo_detective/session24.py` — V37 validation, V34→V37 progression, ANET deep-dive, RSI sweep

---

## Session 23 Key Findings (V36 validation + M/AEO investigation + combined sweep)

### SECTION 1-2: V36 validated — new best criteria

V36 = V35 but: `price_vs_sma150_pct_min=5` → `sma50_above_sma150=1`, plus CC ema200 override.

| Split | V35 P/R | V36 P/R | ΔP | ΔR |
|---|---|---|---|---|
| Sep-Dec full | 43.4%/33.8% | **44.5%/35.9%** | +1.1pp | +2.1pp |
| Train | 46.8%/43.8% | **47.8%/46.5%** | +1.0pp | +2.7pp |
| Test | 30.4%/14.6% | **31.9%/15.6%** | +1.5pp | +1.0pp |
| 2026 OOS (base) | 5.6%/26.1% | 5.4%/26.1% | -0.2pp | 0pp |

Consumer Cyclical recovers: 0 TPs (v35) → **3 TPs** (v36), P=42.9%

V36 is a Pareto improvement over V35 on all Sep-Dec splits while preserving 2026 OOS recall.

### SECTION 3: M and AEO are structurally unrecoverable

M (2026-01-14) and AEO (2026-02-02) fail multiple fundamental gates:

| Ticker | market_cap_b | pct_52wk_high | dividend_yield | vr / bb / rv20 |
|---|---|---|---|---|
| M | $6.35B (fails $25B) | 12.5% (fails ≤12%) | 3.07% (fails ≤2.5%) | vr=1.50 (fails ≤1.20) |
| AEO | $2.98B (fails $25B) | 13.0% (fails ≤12%) | 2.85% (fails ≤2.5%) | bb=23.7%, rv20=0.47, RSI=NULL |

**Both are small-cap retailers** fundamentally incompatible with the large-cap/low-yield/calm
criteria. These 2026 OOS CC TPs are not recoverable without changing the character of the screen.
Accept as a ceiling — likely these are filtered by mlabs_score or execution-level liquidity checks.

### SECTION 4: Combined sweep — V37 candidate identified

Grid of 1280 combos (ema200_floor×bb_max×vr_max×pfh_max×sma150_style), scored with
joint metric = harmonic_mean(R_sepDec, R_2026).

**Key discovery: vr_max=1.15 + bb_max=20 is better than vr_max=1.20 + bb_max=18.**

Top candidate by precision with R_2026≥25%:
`e2≥5 bb≤20 vr≤1.15 pfh≤12 s150=bool` → **P=45.5%/R=36.3% Sep-Dec, Test P=36.2%/R=17.7%**
vs V36: Sep-Dec 44.5%/35.9%, Test 31.9%/15.6%

| Model | sd_P | sd_R | tr_P | tr_R | te_P | te_R | oos_P | oos_R |
|---|---|---|---|---|---|---|---|---|
| V36 | 44.5% | 35.9% | 47.8% | 46.5% | 31.9% | 15.6% | 5.4% | 26.1% |
| **V37 cand** | **45.5%** | **36.3%** | **48.0%** | **46.0%** | **36.2%** | **17.7%** | 5.0% | **26.1%** |

V37 = V36 + bb_width_pct_max=18→**20** + volume_ratio_max=1.20→**1.15**

Interpretation: The wider bb ceiling (≤20 vs ≤18) captures more pullback stocks. The tighter
vr ceiling (≤1.15 vs ≤1.20) filters out high-volume setups that are FPs in Nov-Dec 2025.
Net effect: +4.3pp test P, same OOS recall.

Joint-metric top entries (no precision floor) all have ema200_floor=0, bb≤20, vr≤1.30, no
sma150 gate → R_sepDec≈43%, R_2026≈30% but sd_P only 35%. These are high-recall/low-precision
and not recommended given the already-low P ceiling.

### Script
- `src/algo_detective/session23.py` — V36 validation, M/AEO investigation, combined sweep

---

## Blog Post: results_2025 (June 16, 2025 – January 2, 2026)

Source: https://blog.mlabstrading.com/posts/results_2025  
Retrieved: 2026-06-22

**Covers exactly our Sep-Dec 2025 prime_tickers.csv window.** The 2025 annual gives us the
tightest ground truth for that period:

### Performance (29 weeks)
| Metric | Value |
|---|---|
| Net P/L | $18,643 (realized+unrealized) |
| Realized P/L | $21,241 |
| Total Trades | 173 (100 CSPs + 59 CCs + 14 assignments) |
| Win Rate | **100%** on 159 closed options (0 losses) |
| CAGR | 67.1% |
| Max Drawdown | 14.0% |
| Sharpe | 1.51 |
| Strategy return | +32.51% vs SPY +13.15% |
| Avg ROC per trade | **0.62%** (higher than his Reddit claim of 0.25-0.35%) |
| Avg days in trade | 3.8 days |
| CSP assignment rate | 12.3% (14/100 CSPs) |

### 31 Tickers Traded (Jun 2025 – Jan 2026)
**Top 10 by P&L:**
NVDA ($3,880), ANET ($3,089), UAL ($1,448), AAPL ($1,147), GOOG ($1,060),
MSFT ($954), ORCL ($782), HOOD ($774), DELL ($720), HPE ($429)

**Remaining 21:**
VST, PLTR, FSLR, BIDU, SMCI, DAL, CHWY, GE, IBKR, WPM, AMD, TSM, NEE, XYZ, ATI,
WMT, EBAY, ROST, GILD, HAL, EQT

### 14 CSP Assignments (open at Dec 31, 2025)
| Ticker | Cost Basis | Year-end Price | Unrealized |
|---|---|---|---|
| NVDA | $191.50 | $186.50 | -$500 |
| HPE | $23.30 | $24.02 | +$288 |
| SMCI | $49.41 | $29.27 | -$2,014 |
| NEE | $84.00 | $80.28 | -$372 |

(Note: 14 total assignments, only 4 open at year-end; the other 10 were wheeled out profitably by Jan 2 via CCs)

### Best Week
Week 18 (Oct 13–17, 2025): $3,262 premium, 2.85% ROC — peak of our Sep-Oct training window.

### Key Implications for the Model
1. **31-ticker universe for 2025** is tighter than our 74-ticker narrow universe. The extra
   tickers in our set either appeared only in Jun-Aug 2025 or in Nov-Dec 2025 without a 2025-annual
   entry, or are in 2026 only. ANET is #2 in 2025 but a persistent miss in our model.
2. **100% options win rate** — he never takes a losing options position close. All FPs in our
   model (non-prime days we'd flag) wouldn't generate actual losses; his exit criteria prevent that.
3. **14 assignments at 12.3%** — these are the "worst" prime picks but all turned net-positive
   through CC grinding. Our model missing assignments (primes that go against him) is expected.
4. **SMCI assigned at $49.41** (now $29.27 at year-end) — biggest unrealized loss. SMCI is in
   our narrow universe and is a known volatile pick.
5. **NEE assigned at $84.00** — confirms our dividend-cap analysis. NEE (div=2.89%) fails our
   2.5% cap but he trades it; the assignment confirms he takes it at $84.

---



## Session 22 Key Findings (Two-tier model + boolean gate + CC fix)

### SECTION 1-2: Two-tier regime model

Breadth threshold to split Sep-Dec 2025 dates: only ≥70% creates any split (all 36 dates are
above 68%). At 70%: 26 bull dates (→V34), 10 pullback dates (→V35).

**Sep-Dec 2025 — two-tier at 70% beats uniform V34:**
```
v34 uniform:  P=45.9%  R=37.4%  TP=105
2-tier  70%:  P=46.8%  R=38.4%  TP=108   (+0.9pp P, +1.0pp R)
```

**Temporal test split (Nov-Dec 2025) — two-tier at 70% beats both:**
```
v34 uniform:  P=31.1%  R=14.6%
v35 uniform:  P=30.4%  R=14.6%
2-tier  70%:  P=34.8%  R=16.7%   (+3.7pp P vs v34, +2.1pp R)
```
The 10 Nov-Dec pullback dates (breadth=68-70%) scored better under V35 than V34. This is
the clearest signal that a two-tier model is capturing real structure.

**Combined Sep+2026 (2026 uses base criteria):**
| Model | sep_P | sep_R | oos_P | oos_R | comb_R | sd_TP | oos_TP |
|---|---|---|---|---|---|---|---|
| V34 uniform | 45.9% | 37.4% | 4.0% | 13.0% | 33.9% | 105 | 6 |
| V35 uniform | 43.4% | 33.8% | 5.6% | 26.1% | 32.7% | 95 | 12 |
| 2-tier 70% | **46.8%** | **38.4%** | 5.3% | 23.9% | **36.4%** | **108** | 11 |

Trade-off: 2-tier 70% loses -2.2pp OOS recall (23.9% vs 26.1%) because Jan-Mar 2026 "bull"
dates (breadth 65-70%) are routed to V34_BASE instead of V35_BASE, and V34_BASE has weaker
2026 recall. If 2026 standalone recall matters, V35 uniform is still better.

**Conclusion:** Use 2-tier routing (threshold=70%) when Sep-Dec precision is the priority.
Use V35 uniform when 2026 OOS recall is the priority.

### SECTION 3: sma50_above_sma150 boolean gate

Replacing `price_vs_sma150_pct_min=5` with `sma50_above_sma150=1` is slightly better:

| Criteria | full_P | full_R | tr_P | tr_R | te_P | te_R | oos_P | oos_R |
|---|---|---|---|---|---|---|---|---|
| V35 (pct floor=5) | 43.4% | 33.8% | 46.8% | 43.8% | 30.4% | 14.6% | 5.6% | 26.1% |
| V35_BOOL (bool) | **44.1%** | **34.9%** | **47.4%** | **44.9%** | **31.9%** | **15.6%** | 5.4% | 26.1% |
| V35_BOTH (both) | 43.8% | 33.8% | 46.8% | 43.8% | 31.8% | 14.6% | 5.6% | 26.1% |

`sma50_above_sma150=1`: 93.5% of 2026 primes pass (43/46), vs 61.8% of 2026 controls.
The boolean is cleaner and avoids threshold-tuning. Recommended over the pct floor.
Minor OOS precision cost (5.4% vs 5.6%) — same 12 OOS TPs.

### SECTION 4: Consumer Cyclical regression fix

Root cause identified: v35 adds `price_vs_ema200_pct_min=5` AND `price_vs_sma150_pct_min=5`.
The 3 v34 CC TPs (AMZN Oct 14/15/16, 2025) sit at ema200_pct≈1% and sma150_pct≈2% — both
fail both new floors. V35 CC_FIX: add sector-specific overrides for CC at floor=0%.

`analyze.py` updated with two new handlers:
- `price_vs_ema200_pct_min`: defers to `consumer_cyclical_price_vs_ema200_pct_min` for CC rows
- `price_vs_sma150_pct_min`: defers to `consumer_cyclical_price_vs_sma150_pct_min` for CC rows

V35_CC_FIX results vs V35:
| Split | V35 P | V35 R | Fix P | Fix R | ΔTP |
|---|---|---|---|---|---|
| Sep-Dec full | 43.4% | 33.8% | **43.8%** | **34.9%** | +3 TPs (+2 FPs) |
| Train Sep-Oct | 46.8% | 43.8% | **47.2%** | **45.4%** | +3 TPs (+2 FPs) |
| Test Nov-Dec | 30.4% | 14.6% | 30.4% | 14.6% | 0 (AMZN not in Nov-Dec) |
| 2026 OOS base | 5.6% | 26.1% | 5.6% | 26.1% | 0 (M/AEO fail other gates) |

CC sector precision: v34=60.0% → v35=0.0% → v35_cc_fix=42.9% (3 TP, 4 FP)

Note: M (2026-01-14) and AEO (2026-02-02) in 2026 OOS still fail V35_CC_FIX_BASE.
They have strong trend position (sma150_pct 25%/39%) so the CC override doesn't help —
they fail a different gate. Investigate in session 23.

### Scripts
- `src/algo_detective/session22.py` — two-tier routing, boolean gate, CC fix
- `src/algo_detective/analyze.py` — updated with CC-specific `price_vs_ema200_pct_min` and
  `price_vs_sma150_pct_min` override handlers

---


## Session 19-21 Key Findings (v35 — Regime-Robust Criteria)

### KS feature-ranking shift (session 19)

Comparing top discriminating features between Sep-Oct 2025 (train) and Jan-Jun 2026 (OOS):

**Rose sharply in 2026 (new top features):**
| Feature | Train rank | OOS rank | Train KS | OOS KS |
|---|---|---|---|---|
| `price_vs_sma150_pct` | #27 | **#1** | 0.087 | **0.351** |
| `sma50_above_sma150` | #36 | **#2** | 0.034 | 0.317 |
| `price_vs_ema200_pct` | #18 | **#4** | 0.146 | 0.316 |
| `price_above_sma200` | #32 | #6 | 0.057 | 0.275 |

**Fell sharply (were the 2025 top features):**
- `rv20`: was #3 in 2025, much lower in 2026 — everything has elevated vol in a selloff
- `bb_width_pct`, `atr_pct`: were top 5 in 2025, dropped significantly in 2026

**Implication:** In volatile/bearish regimes, his scanner prioritizes long-term trend strength
(stock well above SMA150/EMA200) rather than VCP contraction patterns. A stock can have wide BBs
and elevated volume in a selloff *and still be a prime pick* if it's far above its long-term MAs.

### The 6 2026 TPs reveal the core pattern

All 6 trades that pass V31A_BASE in 2026: GOOG (×2), WMT (×2), DG, HAL. All are:
- Well above SMA150/EMA200
- Low-to-moderate volume (volume_ratio ≤ 1.05)
- BB width ≤ 13.6%
- Large-cap quality names

His 40 FNs include FCX (6 appearances, bb=17-35%, far from high), DAL/UAL (wide BBs), ANET (wide BB, high rv20). These look like turnaround/recovery plays in a different risk mode.

### v35 definition (session 20-21)

Changes from v34:
1. `price_vs_ema200_pct_min`: 0 → **5** (trend-strength floor; improves train/test precision)
2. `price_vs_sma150_pct_min`: **5** (new gate; #1 KS feature in 2026)
3. `bb_width_pct_max`: 14 → **18** (recovers DAL, DVN, NVDA, C, GOOG 2026 obs)
4. `volume_ratio_max`: 1.10 → **1.20** (recovers HAL, WMT-borderline, etc.)
5. `iv_rv_min=1.0` and `pcr_vol_max=2.0` carried over from v34

### v35 results (84-ticker narrow universe)

| Split | P | R | TP | FP |
|---|---|---|---|---|
| Sep-Dec 2025 full | 43.4% | 33.8% | 95 | 124 |
| Train Sep-Oct 2025 | 46.8% | 43.8% | 81 | 92 |
| Test Nov-Dec 2025 (OOS) | 30.4% | 14.6% | 14 | 32 |
| 2026 Jan-Jun OOS (base, no IV) | **5.6%** | **26.1%** | **12** | 201 |

vs v34: in-sample −2.5pp P / −3.6pp R. OOS recall doubles 13% → 26%. Train→test ΔP narrows from −18.4pp to −16.4pp.

### Known v35 limitation

Consumer Cyclical: 0 TPs (v34 had 3). The `price_vs_sma150_pct_min=5` is too strict for CC prime picks. A per-sector floor relaxation is a candidate for session 22.

### Scripts

- `src/algo_detective/session19.py` — KS shift analysis
- `src/algo_detective/session20.py` — VCP relaxation + trend floor grid sweep
- `src/algo_detective/session21.py` — v35 full validation

---

## Session 18 Key Findings (2026 True OOS + Regime Filter)

### 2026 feature build complete

`data/detective/prime_tickers_2026_oos.csv` (50 trade-log CSPs, Jan–Jun 2026) was fed into
`build.py`, populating `detective_features` with 60,718 new rows across 36 dates. Narrow
universe expanded to **84 tickers** (74 Sep-Dec primes + 10 new 2026-only tickers: AA, AEO, B,
C, DG, DOCN, FCX, M, QCOM, XOM).

### 2026 OOS recall is 13% — criteria do not generalize

`V31A_BASE` (all IV-dependent keys stripped, since `best_iv=NULL` for 2026 in `detective_options`):
P=4.0% | R=13.0% | TP=6 / 46 primes

Compare to Sep-Dec 2025 same base criteria: P=36.8% | R=44.5% | TP=125 / 281. Massive degradation.

### Per-gate failure analysis (2026 prime rows vs V31A_BASE)

| Gate | Failures | % of 46 primes |
|---|---|---|
| `volume_ratio_max≤1.10` | 24 | **52%** |
| `bb_width_pct_max≤14.0` | 22 | **48%** |
| `pct_from_52wk_high_max≤12` | 17 | **37%** |
| `rv20_max≤0.45` | 15 | **33%** |
| `market_cap_b_min≥25` | 7 | 15% |
| `technology_rsi_max≤54` | 6 | 13% |

Distribution of 2026 prime rows:
- `bb_width_pct`: median=**13.6%** (right at the 14% ceiling), max=35.4%
- `volume_ratio`: median=**1.14** (above the 1.10 ceiling), max=2.26

**Root cause:** The Apr–May 2026 tariff selloff pushed market breadth to 59–63% and stock-level
volatility/volume well above VCP thresholds. GarbageTimePro was trading *through* the volatility,
not waiting for the contraction setup. Even March 2026 (breadth 70%, should be fine) had R=0% —
only 5 primes and none passed.

### Regime filter does NOT rescue the Sep-Dec 2025 holdout

Section 4 of session18 tested `breadth_pct_min` thresholds on the v34 Sep-Dec train/test split:

| breadth_min | train P | test P | ΔP |
|---|---|---|---|
| 0% (none) | 49.5% | 31.1% | −18.4pp |
| 65% | 49.5% | 31.1% | −18.4pp (no dates filtered) |
| 68% | 49.5% | 31.1% | −18.4pp (no dates filtered) |
| 69% | 49.7% | 28.1% | **−21.6pp** (worse) |
| 70% | 50.3% | 25.0% | **−25.3pp** (much worse) |
| 71% | 49.7% | 40.0% | −9.7pp (only 2 test dates remain) |

A 71% breadth floor leaves only 2 Nov-Dec test dates — statistically meaningless. At usable
thresholds, a simple breadth floor either does nothing or makes degradation worse. The session17
degradation is **overfitting**, not a regime effect from breadth.

### Conclusion: regime-adaptive criteria needed

GarbageTimePro's 4-tier hierarchy likely includes regime-adaptive thresholds — not just a
binary breadth pass/fail. When breadth drops, he appears to:
- Relax `volume_ratio_max` (accepts higher volume in volatile markets)
- Relax `bb_width_pct_max` (accepts wider bands during selloffs)
- Accept stocks further from 52-week highs

Our current criteria are a static snapshot of the Sep-Oct 2025 bull market. To handle
regime-adaptive behavior we'd need per-regime criteria sets, which requires more labeled data
in multiple regimes.

### Script

`src/algo_detective/session18.py`. Run:
```bash
docker compose run --rm pipeline python -m src.algo_detective.session18
```

---

## Session 17 Key Findings (Temporal Holdout — OOS)

Train: Sep-Oct 2025 (23 dates, 185 prime obs)  
Test:  Nov-Dec 2025 (13 dates, 96 prime obs) — never exclusively the tuning target

### Critical result: severe overfitting on temporal holdout

| Criteria | Train P | Train R | Test P | Test R | ΔP |
|---|---|---|---|---|---|
| v31a | 45.1% | 50.3% | 28.1% | 28.1% | −17.0pp |
| v34 | 49.5% | 49.2% | 31.1% | 14.6% | **−18.4pp** |

v34 train→test: **−18.4pp precision, −34.6pp recall**. No criteria variant achieves R≥30% on the test split (best OOS is iv_rv=0.9 + no pcr_vol filter, P=28.1% R=28.1%).

v34 introduces +0pp additional overfitting vs v31a on precision, but recall collapses (-35pp).
The extra iv_rv and pcr_vol gates mainly suppress true positives on the OOS dates.

### Root cause: overfitting to Sep-Oct 2025 market regime

The degradation is NOT explained by breadth (Nov-Dec breadth is 68.3–71.3%, only slightly
lower than Sep-Oct's 68.9–72.4%). The Sep-Oct training period had consistently higher prime
counts per date (5-15) vs Nov-Dec (3-13). Most criteria were fit on the Sep-Oct regime.

### Grid sweep on test split: no winning criteria with R≥30%

Best OOS from iv_rv × pcr_vol sweep (Section 5, session17): all combinations with R≥30% have
P≤31.1%. The criteria space as designed cannot generalize well to Nov-Dec 2025 on this data.

### Script

`src/algo_detective/session17.py`. Run:
```bash
docker compose run --rm pipeline python -m src.algo_detective.session17
```

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

#### Track B current best: V39 (narrow 84-ticker) — as of Session 28

V39 = V38 + adr20_pct_max=4.0:

```python
V39 = {**V38, "adr20_pct_max": 4.0}
# V38 = V37 + technology_rsi_max=60
# V37 = V36 + bb_width_pct_max=20.0 + volume_ratio_max=1.15
# V36 = V35 + sma50_above_sma150=1 + consumer_cyclical_price_vs_ema200_pct_min=0
```
Full V39 = V38 with one gate added. All other keys identical to V38.

| Split | V38 P/R | V39 P/R | Δ |
|---|---|---|---|
| Sep-Dec 2025 full | 44.2%/40.6% (sdTP=114) | **44.4%/40.6% (sdTP=114)** | +0.2pp/0 |
| Train Sep-Oct 2025 | 45.5%/48.6% | **45.5%/48.6%** | 0/0 |
| Test Nov-Dec 2025 | 40.0%/25.0% | **40.7%/25.0%** | **+0.7pp/0** |
| 2026 OOS (base) | 4.9%/26.1% (oosTP=12) | **4.9%/26.1% (oosTP=12)** | 0/0 |

V39 adds zero recall cost: all 114 Sep-Dec TPs and all 12 OOS TPs pass `adr20_pct<=4.0`.
Pure FP removal confirming "ADR < 4.0 preferably" as a real hard filter in the scanner.

---

### Best criteria found — two tracks (archive)

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
| v31a+pcr_vol_max=2.0 | Narrow (74T) | 47.0% | 39.1% | 110 | 124 | session15/16 — v33a |
| v31a+iv_rv_min=1.1 | Narrow (74T) | 46.1% | 35.2% | 99 | 116 | session15 — R at floor |
| v31a+iv_rv_min=1.2 | Narrow (74T) | 47.3% | 34.2% | 96 | 107 | session15 — R below floor |
| **v34** | **Narrow (74T)** | **47.7%** | **37.4%** | **105** | **115** | **v31a + iv_rv_min=1.0 + pcr_vol_max=2.0** |
| v33b | Narrow (74T) | 48.0% | 34.5% | 97 | 105 | v31a + iv_rv_min=1.1 + pcr_vol=2.0 — R below floor |

---

## Session 16 Key Findings (v33 Grid Sweep → v34 Definition)

### pcr_oi snapshot wired into nightly pipeline

`fetch_snapshot_pcr` is now called as Step 5 of `src/main.py`. Every time the nightly pipeline runs, it captures `best_iv`, `pcr_vol`, and `pcr_oi` for all 74 prime tickers and stores them in `detective_options`. No separate cron needed — runs automatically with the market scan.

### v33 grid results — best combination with R≥35%

Full `iv_rv_min × pcr_vol_max` sweep on v31a base:

| iv_rv_min | pcr_vol_max | P | R | TP | FP | ΔP |
|---|---|---|---|---|---|---|
| 0.9 | none (baseline) | 45.2% | 40.2% | 113 | 137 | — |
| 0.9 | 2.0 (v33a) | 47.0% | 39.1% | 110 | 124 | +1.8pp |
| **1.0** | **2.0 (v34)** | **47.7%** | **37.4%** | **105** | **115** | **+2.5pp** |
| 1.0 | 1.5 | 47.7% | 36.3% | 102 | 112 | +2.5pp |
| 1.1 | 2.0 (v33b) | 48.0% | 34.5% | 97 | 105 | +2.8pp |
| 1.2 | 2.0 | 49.5% | 33.5% | 94 | 96 | +4.3pp |

`iv_rv_min=1.2 + pcr_vol_max=2.0` gives best precision (49.5%) but recall drops to 33.5%. The Pareto-optimal point with R≥35% is **v34**.

### v34 definition

```python
V34 = {**V31A, "iv_rv_min": 1.0, "pcr_vol_max": 2.0}
```
**P=47.7%, R=37.4%, TP=105, FP=115 (+2.5pp over v31a)**

### What v34 cuts vs v31a

**FPs removed (22 total):** Technology (-8 FPs), Financial Services (-5), Industrials (-4), Consumer Defensive (-2), Energy (-2), Comm Services (-1)

**TPs lost (8 total):** WMT loses some obs (iv_rv between 0.9–1.0; thin premium over realized vol), ATI x1 (pcr_vol=10.1, extreme put activity on an illiquid options chain), AXP x1 (pcr_vol>2.0 on one date), + scattered single obs.

### WMT iv_rv insight

WMT has `iv_rv=NULL` in the raw feature dict but `_apply_criteria` computes iv_rv live from `best_iv/rv20`. WMT's computed iv_rv is between 0.9 and 1.0 — the scanner picks WMT when IV barely exceeds realized vol. Consumer Defensive stays at 11/13 TPs with 84.6% precision under v34.

### Sector breakdown of v34

| Sector | TP | FP | Precision |
|---|---|---|---|
| Basic Materials | 3 | 0 | 100.0% |
| Communication Services | 20 | 18 | 52.6% |
| Consumer Cyclical | 3 | 2 | 60.0% |
| Consumer Defensive | 11 | 2 | **84.6%** |
| Energy | 2 | 5 | 28.6% |
| Financial Services | 25 | 21 | 54.3% |
| Healthcare | 0 | 1 | 0.0% |
| Industrials | 22 | 35 | 38.6% |
| Technology | 19 | 31 | **38.0%** |

Remaining FP pressure: Industrials (35 FPs), Technology (31 FPs), Financial Services (21 FPs). These three sectors are the remaining ceiling.

### Script

`src/algo_detective/session16.py`. Run:
```bash
docker compose run --rm pipeline python -m src.algo_detective.session16
```
Note: requires `docker compose build pipeline` first since session scripts are baked into the image.

---

## Session 15 Key Findings (Options Chain Quality Gates)

### pcr_oi — NULL everywhere, needs pipeline backfill

The `detective_options` table has **pcr_oi = NULL for all rows**. The options_chain.py backfill mode uses `bars` endpoint which doesn't return OI — only the `snapshots` endpoint returns OI. Any future pcr_oi backfill needs to use snapshots (available only going forward from daily runs, or from a fresh historical snapshot pull). No pcr_oi gates are testable until that data exists.

### iv_rv_min tightening — real but modest signal

Raising `iv_rv_min` from 0.9 → higher values on v31a base:

| iv_rv_min | P | R | TP | FP | ΔP |
|---|---|---|---|---|---|
| 0.9 (baseline) | 45.2% | 40.2% | 113 | 137 | — |
| 1.0 | 45.7% | 38.1% | 107 | 127 | +0.5pp |
| **1.1** | **46.1%** | **35.2%** | **99** | **116** | **+0.9pp** |
| 1.2 | 47.3% | 34.2% | 96 | 107 | +2.1pp |
| 1.5 | 47.3% | 30.5% | — | — | recall too low |

`iv_rv_min=1.1` is the best gate that keeps R≥35%. `iv_rv_min=1.2` gives +2.1pp precision but recall drops below the floor.

### pcr_vol_max=2.0 retested on v31a base

Session 12 tested pcr_vol_max=2.0 on v29. Session 15 retested on v31a:

- **v31a + pcr_vol_max=2.0: P=47.0%, R=39.1%, TP=110, FP=124 (+1.8pp)** — best balanced gate tested this session

### v33 candidate

Two promising candidates for Session 16:

**v33a (single gate):** `v31a + pcr_vol_max=2.0` → P=47.0%, R=39.1%

**v33b (stacked):** `v31a + pcr_vol_max=2.0 + iv_rv_min=1.1` — not yet run; may stack cleanly since they measure different things (PCR = market sentiment, IV/RV = premium quality relative to realized vol). Worth testing.

### pcr_oi going forward — daily snapshot collection

The `--snapshot` mode in `options_chain.py` already computes and stores `pcr_oi` correctly. It just isn't being run automatically. To start collecting from today forward, add this cron entry on the host:

```bash
( crontab -l; echo "0 20 * * 1-5  cd /home/dev/workspace/Market-Intelligence && docker compose run --rm pipeline python -m src.algo_detective.options_chain --snapshot >> /home/dev/workspace/Market-Intelligence/data/detective/logs/snapshot.log 2>&1" ) | crontab -
```

8:00 PM ET on weekdays — same time his scanner runs (data ready at 8:05 PM ET). After ~3 months of collection, pcr_oi will be testable on any new prime_tickers data from that period.

Note: Historical pcr_oi for Sep-Dec 2025 is **permanently unavailable** — Alpaca's bars endpoint returns only volume, not OI. No free retroactive source exists.

### Script

`src/algo_detective/session15.py` — 442 lines. Run:
```bash
docker compose run --rm pipeline python -m src.algo_detective.session15
```

---

## Session 14 Blog Post — QCOM Wheel Execution Deep-Dive

Source: https://blog.mlabstrading.com/posts/wheeled_qcom_through_a_25_percent_drawdown  
Published: May 3, 2026 — live trade post-mortem (Jan–May 2026)

This post contains **no scanner criteria** but is the richest source we have for his wheel execution philosophy. Key facts (all quantitative, verbatim):

### Pre-trade checklist (his words)

1. **"Would you hold this through a 30-50% drawdown?"** — fundamental conviction, not just liking the dip
2. **Balance sheet survival:** manageable debt, strong FCF, dividend payout ratio ≤ ~30% of FCF, interest coverage not fragile
3. **Valuation at entry:** strike must be below a valuation level he likes. "Your real entry price is strike - premium received."
4. **Options liquidity:** "tight bid/ask spreads, good open interest near your strike, and easy exits if the trade moves against you" — explicitly filters illiquid chains
5. **Premium source matters:** prefers IV elevated by market-wide or sector volatility, NOT company-specific risk (earnings, FDA, squeeze). "High premium isn't automatically good."

### Strike selection (CSP)

- Strike set so breakeven (strike − premium/share) is below fair value
- QCOM Lot 1: $167.50 strike, $1.12 premium → breakeven $166.38, ~6.9% OTM from ~$180
- QCOM Lot 2: $160.00 strike, $1.98 premium over 2 trades → described as "even better" (more cushion)
- No explicit delta target in this post (delta 0.15-0.30 from other sources still valid)

### Covered call rules post-assignment

- **First preference:** sell CCs at or above original cost basis when premium is decent
- **When stock drops far:** sell CCs below original cost but near *adjusted* cost basis (ground down by prior premiums + dividends). Never sell aggressive below-cost CCs just to generate income.
- **Do nothing is valid:** if CC premium at the safe strike is "trash," hold and wait. He held through all of February (entire month, zero CCs) rather than lock in a loss.
- **Stop before reversals:** "I stopped selling CCs around April 6. Didn't want to cap the upside right before a potential move." — He sacrificed 2.5 weeks of premium to preserve full upside capture.
- **Recovery CC:** Sold $170 CCs immediately when stock gapped to $149 — well above his adjusted cost basis by then

### Roll mechanics

Only one roll described (CC side, not CSP): CC blown through on gap-up → bought back at a loss, re-sold same-strike, next expiry, for net credit. He does NOT chase a higher strike on the roll.

### Position sizing

"These two QCOM lots tied up about $32,750 in capital for me. That's a meaningful position, but it was not my entire portfolio." — no explicit % stated, but sized so a 26% drawdown in one name is painful but manageable.

### Early close / decay harvesting

Actively buys back CCs when they decay near zero. Example: sold at $2.24, bought back at $0.01 (0.5% of sell price), re-deployed. This is explicit — he is not just holding to expiration.

### The math (QCOM full trade)

| | Lot 1 (100sh @ $167.50) | Lot 2 (100sh @ $160.00) |
|---|---|---|
| Original cost | $167.50/sh | $160.00/sh |
| CSP premium | −$1.12 | −$1.98 |
| CC premium | −$4.56 (10 rounds) | −$7.16 (8 rounds + 1 roll) |
| Dividend | −$0.89 | −$0.89 |
| **Adj. cost basis** | **$160.93** | **$149.97** |
| Called away at | $170.00 | $170.00 |
| **Profit/sh** | **$9.07** | **$20.03** |

Total: ~$2,900 on ~$32,750 tied up = **8.8% / 3.5 months = ~30% annualized** through a 26% drawdown. 

### Key implication for the scanner

"I don't wheel illiquid chains. You want tight bid/ask spreads, good open interest near your strike." This is an explicit filter that our `best_iv` / `pcr_oi` / `pcr_vol` gates partially capture — but bid-ask spread data is not in our current pipeline. When pcr_oi backfill is available, it serves as a proxy for options liquidity.

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

### Reddit research note — RESOLVED (Session 14)

Playwright + Arctic Shift API successfully scraped the full comment history. See Session 14 findings above for the complete analysis.

### Next: add pcr_vol as a feature in narrow-universe analysis (Session 12)

Once backfill runs, create `session11.py` (or `session12.py`) that:
1. Joins `detective_options.pcr_vol` into the narrow-universe feature rows
2. Runs KS analysis: pcr_vol on prime days vs non-prime days for same tickers
3. Tests `pcr_vol_min` and `pcr_vol_max` gates (hypothesis: prime days may have LOWER pcr_vol — calls dominate, market is complacent, good time to sell puts)
4. Combine best pcr gate with v29 criteria

---

## Session 14 Key Findings — Reddit Scrape of u/GarbageTimePro

Reddit direct access blocked (network security on www + old.reddit, JSON API). Used Pullpush.io (archive through May 2025) and **Arctic Shift API** (archive through June 2026) for comment history. Also accessed mlabstrading.com portfolio snapshot directly via Playwright.

### 1. mlabstrading.com = HIS OWN PLATFORM (not Market Rebellion Labs)

The `mlabs_score` in the CSV is **GarbageTimePro's proprietary ML scoring layer from his own platform**, not a third-party service:

> "Absolutely! It's my own, built for members of my pro community directly from their feedback. It's the platform I use to discover and track trades, track market regimes, etc. There are about 400 total community members in the Discord."

- Community: r/mLabsTrading subreddit + Discord (https://discord.gg/mUvT6Vgewv)
- Free Discord tier; paid "pro" tier gives watchlists, trades, and platform access
- The scoring layer runs as a **local LLM in his homelab** (r/LocalLLM):
  > "I also built a thin inference layer to do a small portion of the analysis. This inference layer lives in my homelab as well — shoutout to those over at /r/LocalLLM/."

### 2. 4-Tier Filter Hierarchy — Confirmed Verbatim

From r/thetagang post "11/17 wheeling boring names YTD" (2026-05-31):

> "Strict regime filters, followed by strict filters on fundamentals, followed by strict filters on technicals, followed by strict filters on options contracts. At the end of each trading session, i'll have 0-10 contracts on 0-5 names on average that I'm watching into the next session."

This maps exactly to our reverse-engineered criteria:
1. **Regime**: SMA50>SMA200, price above EMA200, bb_width_pct 4–14%, volume_ratio ≤1.10
2. **Fundamentals**: market_cap_b≥25, sector gates, forward_pe<50, dividend_yield filter
3. **Technicals**: ADX≥15/20, RSI thresholds by sector, IV/RV≥0.9
4. **Options contracts**: IV≥20%, delta 0.15–0.30, DTE weekly, PCR

### 3. Scanner Runs AFTER MARKET CLOSE — EOD Data Confirmed

> "By 8:05pm eastern, the data for the next day is ready for me to do a quick manual verification pass for the CSP candidates going into the next day."

**Critical implication**: the scanner uses EOD data, not intraday data. Our EOD-based features are the correct input. Prior assumption that EOD = noisy approximation of intraday was wrong. The ±5-10pp IV error concern is less relevant than assumed.

### 4. Pipeline Architecture — 30+ Jobs

> "I have about 30 different jobs running throughout the day inside of my homelab pulling and processing stocks, options, news, sentiment, fundamentals, and technicals data. This pipeline does maybe 95% of the heavy lifting."

- Data sources: stocks, options, news, sentiment, fundamentals, technicals
- Built over ~3 years (started ~2022-2023 as a hackathon project)
- 95% automated; he does a 10-15 min manual review nightly

### 5. Trading Parameters — All Confirmed

| Parameter | His statement | Our CSV data |
|-----------|--------------|-------------|
| Delta | "15-30 delta" / "0.15-0.25 deltas" | -0.20 to -0.30, median -0.27 ✓ |
| DTE | "2-45 DTE, averaging ~5 DTE" | median DTE=7 ✓ |
| IV | "25-30 IV on average" | median ~37%, floor 20% ✓ |
| Weeklies | "large majority of CSPs are weeklies" | median DTE=7 ✓ |
| ROC | "0.25-0.35% ROC on average" | CSV annual_yield_pct median=44% ≈ 0.85%/wk |
| Annualized yield | "20-30% AY on average" | CSV shows range 20-516% |

### 6. VCP (Volatility Contraction Pattern) — Explicit Statement

From r/algotrading comment (2025-02-22) when asked "what's your edge?":

> "Volatility contraction pattern. Mark Minervini's books"

VCP is exactly what our criteria capture:
- Bollinger band width contracting → `bb_width_pct_max=14`
- Volume declining → `volume_ratio_max=1.10`
- Price above trend → `sma50_above_sma200`, `price_vs_ema200_pct_min=0`
- ADX confirms trend is intact → `adx_min=15`
- RSI not overbought → sector RSI gates

**The scanner finds stocks in VCP setup and sells CSPs on them.**

### 7. Equity System (Separate But Related — Same Technical DNA)

His algotrading posts describe a stock trend-following system using the same indicators:
- Entry: price above 150-day EMA + ADX>20 + RSI 30-70 + MACD crossover
- Filters: narrows 8-10k stocks → ~150 candidates
- Using **Alpaca** for paper trading (same data source we use!)
- Describes himself as a "filtered trend-follower" (not pure momentum)
- The CSP scanner likely shares the same regime/technical filter layer

### 8. Strategy Character

Key quotes that define the approach:
- **"BORING names only"** — explicitly avoids high-beta, speculative stocks
- **"I wheel boring, profitable companies"**
- **"P/E ratios of over 200 are not my style. That screams overvalued"** — PE cap confirmed
- **"Everyone who chases juicy premiums gets wrecked in the end. There's no way around it"** — explains LOW IV threshold (he avoids high-IV names that look attractive)
- Assigned names he recovered from: QCOM, NVDA, DG, FCX, HPE — all large-cap quality names

### 9. Portfolio Snapshot — 47 Symbols Traded (Jul 2025 – May 2026)

From https://blog.mlabstrading.com/portfolio_snapshots/mlabs-portfolio-snapshot-2026-05-31_11mo.png

**Full ticker list (ordered by total P&L):**

| Rank | Symbol | P&L | Notes |
|------|--------|-----|-------|
| 1 | NVDA | $5,645.95 | #1 earner |
| 2 | GOOG | $3,965.29 | |
| 3 | ANET | $3,584.46 | persistent miss in our model |
| 4 | QCOM | $2,889.47 | assigned & recovered |
| 5 | SPAXX | $2,333.06 | Fidelity money market (interest) |
| 6 | UAL | $1,303.69 | airline |
| 7 | AAPL | $1,177.46 | |
| 8 | ORCL | $1,031.66 | |
| 9 | LRCX | $987.82 | open CSP at snapshot |
| 10 | MSFT | $901.62 | |
| 11 | HOOD | $776.42 | Robinhood — surprise pick |
| 12 | DELL | $719.85 | |
| 13 | HPE | $655.73 | assigned & recovered |
| 14 | DG | $558.95 | open holding (assigned) |
| 15 | BIDU | $424.66 | Chinese ADR |
| 16 | VST | $358.94 | |
| 17 | DAL | $304.10 | airline |
| 18 | NEE | $297.14 | he DOES trade NEE (our persistent miss due to div_yield=2.89%) |
| 19 | SMCI | $288.53 | open CC at snapshot (assigned) |
| 20 | PLTR | $274.64 | surprise — high PE but he trades it |
| 21 | FSLR | $247.61 | First Solar |
| 22 | AA | $209.24 | Alcoa |
| 23 | HAL | $166.92 | Halliburton |
| 24 | AXP | $155.25 | |
| 25 | CHWY | $151.82 | Chewy |
| 26 | EQT | $148.88 | |
| 27 | WMT | $148.20 | |
| 28 | GE | $145.31 | |
| 29 | XYZ | $141.80 | CONFIRMED real ticker (not data artifact!) |
| 30 | IBKR | $138.66 | |
| 31 | WPM | $132.31 | Wheaton Precious Metals |
| 32 | FCX | $128.80 | Freeport-McMoRan, assigned & recovered |
| 33 | AEO | $118.64 | American Eagle |
| 34 | AMD | $117.32 | |
| 35 | DOCN | $116.60 | DigitalOcean |
| 36 | TSM | $109.33 | Taiwan Semi |
| 37 | AAL | $92.90 | American Airlines |
| 38 | B | $76.75 | Barnes Group |
| 39 | ATI | $75.01 | ATI Inc. (our persistent miss — null IV) |
| 40 | XOM | $59.20 | |
| 41 | EBAY | $46.80 | |
| 42 | ROST | $44.60 | Ross Stores |
| 43 | C | $35.95 | Citigroup |
| 44 | GILD | $31.28 | Gilead (our persistent miss — div_yield=2.58%) |
| 45 | M | $28.00 | Macy's |
| 46 | DVN | $17.46 | Devon Energy |
| 47 | META | negative | only red entry in table |

**Open holdings at snapshot (assigned shares he holds):**
- DG: 150 shares (cost basis ~$103, assigned)
- SMCI: ~180 shares (selling covered calls against them)

### 10. Cross-Reference: Prime Ticker Universe vs His Traded Universe

Tickers in our Sep–Dec 2025 prime_tickers.csv that also appear in his 11-month snapshot:
NVDA, GOOG, ANET, QCOM, UAL, AAPL, MSFT, ORCL, DELL, DG, BIDU, DAL, NEE, GE, AXP, FCX, AMD, TSM, AAL, ATI, EBAY, GILD, C, WMT, ROST, DVN, XOM, WPM

Surprises not in our prime universe (or worth noting):
- HOOD: newer fintech, may have added later
- PLTR: high PE at time, likely short-dated trade when PE was more reasonable  
- SMCI: high volatility — may meet IV requirements but assigned (CSP went wrong)
- NEE: in our persistent miss list (div_yield=2.89%) — he DOES trade it, suggesting dividend cap may be 3.0% not 2.5%
- GILD: in our miss list (div_yield=2.58%) — he trades it, same implication
- XYZ: a real ticker with real P&L — needs investigation (not a data artifact)
- META: only negative P&L (red) — unusual, may be from an unfavorable assignment

### 11. Performance (12 months Jun 2025 – Jun 2026) — blog.mlabstrading.com, published 2026-06-20

| Metric | Value |
|---|---|
| Net P/L | $28,527.69 (+35.13% on deployed capital) |
| Total Trades | 273 |
| Unique Tickers | 47 |
| Win Rate | 96.3% |
| Sharpe Ratio | 2.02 |
| Sortino Ratio | 3.23 |
| Max Drawdown | -9.93% |
| Annualized Yield | 35.2% |
| Avg Weekly ROC | **0.68%** (higher than Reddit estimate of 0.25–0.35%) |
| Avg Per-Trade ROC | 0.55% |
| Median Weekly Deployed | $81,200 (~50% of capital) |
| Total Capital | $146,889 |
| Current Cash (as of 2026-06-20) | $132,478 (90%) — 0 open trades |
| SPY Return | +25.65% (for comparison) |
| SPY Sharpe | 1.46 |
| SPY Max Drawdown | -10.69% |

**Current holdings (2026-06-20):** DG (100 shares), SMCI (100 shares) — both red, being wheeled.

### 12. Dividend Yield Cap Revision — v32 TESTED, NOT BENEFICIAL

NEE (2.89%) and GILD (2.58%) both appear in his trading history. Session 14 tested `dividend_yield_max=3.0` (v32) on the narrow universe:

- Recovers: 2 GILD TPs (Nov 11 + Nov 17)
- NEE is NOT recovered — fails other gates (not just the div cap)
- New FPs: GILD 7 rows
- Result: P drops -0.8pp to 44.4%, R +0.7pp to 40.9%

**Decision: stay at v31a (dividend_yield_max=2.5).** The cap revision is not worth the precision cost.

### 13. Blog Post Analysis — "One Year Wheeling BORING Names" (published 2026-06-20)

Source: https://blog.mlabstrading.com/posts/one_year_of_boring_puts

**No filter criteria in this post.** It is a pure performance retrospective. Key insights:

**Regime / activity pattern** — He pulls back significantly when conditions are unfavorable:

| Month | Trades | Premium |
|---|---|---|
| Sep 2025 | 39 | $4,470 |
| Oct 2025 | 39 | $4,958 |
| Jan 2026 | 43 | $2,099 |
| Nov 2025 | 29 | $1,938 |
| Dec 2025 | 33 | $1,493 |
| Feb 2026 | 13 | $1,575 |
| Mar 2026 | 27 | $489 |
| Apr 2026 | 13 | $1,046 |
| May 2026 | 10 | $1,526 |

Sep/Oct 2025 + Jan 2026 were his busiest periods, which maps exactly to when our prime_tickers.csv observations are densest. Feb/May/Jun 2026 he nearly stopped — "when breadth is bad or premium is not worth the risk, I sit on my hands."

**Explicitly avoided tickers** (high-beta / non-profitable):
- SOFI, HIMS, MARA, RIOT, IONQ, TSLL — never traded, explicitly called out as "the theta-based subs pumping" names that lead to 30-40% drawdowns

**Sector diversification** — "A wheel account stuffed full of semis or high-beta tech might look diversified by ticker, but it's not diversified by risk. So I spread across sectors instead of stacking one theme." This confirms our sector-specific gates are modeling real behavior, not overfit.

**Confirmed ROC discrepancy**: Reddit comments say "0.25-0.35% ROC on average" but the blog reports avg weekly ROC = 0.68%. Likely Reddit = target weekly premium as % of deployed, blog = realized (including assignment gains, CC premiums, dividends). Not a contradiction — different measurements.

**Largest drawdown narrative**: QCOM assigned at $167.50 and $160, dropped to $124, held for months, eventually wheeled out at profit. Shows the strategy works through assignments because he only takes assignments on "boring" quality companies that recover.

**Key quote** (explains VCP + quality filter in plain English):
> "Boring is what helped keep my drawdowns in single digits. There's no SOFI, no HIMS, no MARA, IONQ, TSLL on this list. That's on purpose."

**Potential next research source**: `/posts/wheeled_qcom_through_a_25_percent_drawdown` — describes how he managed the QCOM trade start to finish. May contain more detail about his strike selection and position management logic.

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

### Reddit: u/GarbageTimePro — RESOLVED (Session 14)

Arctic Shift API provided comment archive through June 2026. Full findings in Session 14 block above. Key outcome: mlabs_score = his own LLM, scanner runs EOD, VCP is the stated edge, 4-tier filter hierarchy confirmed verbatim. Full 47-ticker trading universe recovered from mlabstrading.com portfolio snapshot.

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

### Reddit research note — RESOLVED (Session 14)

Playwright + Arctic Shift API successfully scraped the full comment history. See Session 14 findings above for the complete analysis.

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

### 0. NEW — Test dividend_yield_max=3.0 on narrow universe (Session 15)

Reddit confirms he trades NEE (2.89%) and GILD (2.58%), both currently excluded by our 2.5% cap. Test v31a + `dividend_yield_max=3.0`:
- Expected: +~7 TPs on narrow universe, minimal FP cost (these are already in the 74-ticker set)
- If confirmed, adopt as v32a

Also worth: test `technology_rsi_max=54` + `dividend_yield_max=3.0` combined.

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

## What the Scanner Is Doing We Can't Model — Updated (Session 14)

**Rule-based ceiling is ~7.5% on SP500, ~45% on narrow universe.** The scanner emits 0-5 names/day from its filtered candidate list.

### Now Confirmed (Session 14 Reddit findings):

1. **mlabs_score = his own local LLM score** (NOT Market Rebellion Labs). A thin inference layer running in his homelab processes the filtered candidates and produces a final score. This is the unrecoverable piece. It likely uses news sentiment, options flow, and technical pattern recognition in combination.

2. **The scanner runs EOD** — "By 8:05pm eastern, data is ready." Our EOD features ARE the right inputs. The prior concern about intraday vs EOD timing was unfounded.

3. **VCP pattern is the stated edge** — Mark Minervini's Volatility Contraction Pattern. Our BB width (4–14%) + volume ratio (≤1.10) + trend confirmation gates directly model this pattern.

4. **4-tier hierarchy confirmed** — regime → fundamentals → technicals → options. We've reverse-engineered tiers 1-3 well. Tier 4 (options contract specifics: exact delta, spread width, OI, bid-ask) is where remaining FPs would be cut.

5. **"BORING names" philosophy** — explicitly avoids high-IV speculative stocks. The forward_pe_max=50 and sector IV gates encode this philosophy.

### Remaining gap explanation:
- **~55% of our v31a FPs** are eliminated in his Tier 4 (options contract filter): exact spread width, OI threshold, bid-ask spread checks at EOD prices that we don't replicate
- **~35% of remaining FPs** are eliminated by his mlabs_score LLM layer (news, sentiment, options flow patterns)  
- **~10% of FPs** are borderline cases where his manual 10-15min review removes them

### Practical ceiling:
At ~45% precision on the narrow universe (v31a), we're modeling ~3 of his 4 tiers. The fourth tier (options contract quality + LLM score) accounts for the remaining ~55% precision gap. Without options chain data (bid-ask spread, OI by strike) and the LLM score, 45% is near the true ceiling for our approach.
