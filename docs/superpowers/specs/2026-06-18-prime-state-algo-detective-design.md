# Prime State Algo Detective — Design Spec

**Date:** 2026-06-18
**Status:** Approved for implementation

---

## Context

A friend's trading scanner generates tickers in "prime states" — a composite of technical
trend conditions, macro regime filters, and options-specific criteria. We have a CSV of ~200
(date, ticker) pairs representing confirmed prime-state appearances from September–December 2025.

The goal is to reverse-engineer the criteria by finding what distinguishes those tickers from
the broader universe on those same dates. The end deliverable is a validated criteria set that
wires into the existing CSP scanner as an additional filter layer.

Key constraints:
- The analysis must be **discriminative** — compare prime tickers against the full universe
  on the same dates, not just profile the prime tickers in isolation
- Optimize for **high precision** — when criteria fire, it should almost always be right;
  occasional misses are acceptable
- Results must **persist across sessions** — feature matrix computed once, reused forever
- All 77 unique prime-state tickers are already in `universe_daily_ohlcv` (confirmed);
  OHLCV history goes back to May 2024, covering all CSV dates with ample lookback

---

## Module Structure

New standalone module `src/algo_detective/`. Read-only relative to production code — it only
writes to two new `detective_*` tables and to `data/detective/`. Does not touch the CSP
screener, watchlist, or existing pipeline.

```
src/algo_detective/
├── __init__.py
├── ingest.py        — parse the prime-state CSV; emit PrimeTicker records
├── features.py      — compute all indicators for a (ticker, date) from stored OHLCV
├── universe.py      — build the per-date control group from the 2244-ticker store
├── macro_context.py — pull VIX/posture/SPY/sector state from daily_signals + digests
├── store.py         — detective_features and detective_macro SQLite tables + DDL
├── build.py         — orchestrator: ingest CSV, compute all features, populate tables
├── analyze.py       — rank features by discriminating power; produce criteria candidates
└── validate.py      — test a criteria JSON against the full labeled dataset
```

The CSV file lives at `data/detective/prime_tickers.csv` (committed to the repo).

CLI entry points (run inside Docker):
```bash
python -m src.algo_detective.build                          # idempotent — skips already-computed rows
python -m src.algo_detective.build --inspect 2025-10-07     # print feature summary for a specific date
python -m src.algo_detective.analyze                        # outputs JSON/CSV to data/detective/
python -m src.algo_detective.validate --criteria data/detective/criteria_v1.json
```

---

## Feature Matrix

Computed for every `(ticker, date)` pair — both prime (is_prime=1) and control (is_prime=0).
All values are as-of market close on that date, using only data at or before that date
(no lookahead). OHLCV sourced from `universe_daily_ohlcv`. Indicators via `pandas_ta`
(already a project dependency, used in `src/screener/options.py`).

### Trend & EMA/SMA position

| Feature | Description |
|---|---|
| `ema20`, `ema50`, `ema150`, `ema200` | Exponential moving averages |
| `sma20`, `sma50`, `sma150`, `sma200` | Simple moving averages |
| `price_vs_ema20_pct` | `(close - ema20) / ema20 * 100` |
| `price_vs_ema50_pct` | `(close - ema50) / ema50 * 100` |
| `price_vs_ema150_pct` | `(close - ema150) / ema150 * 100` |
| `price_vs_ema200_pct` | `(close - ema200) / ema200 * 100` |
| `price_vs_sma20_pct` | `(close - sma20) / sma20 * 100` |
| `price_vs_sma50_pct` | `(close - sma50) / sma50 * 100` |
| `price_vs_sma150_pct` | `(close - sma150) / sma150 * 100` |
| `price_vs_sma200_pct` | `(close - sma200) / sma200 * 100` |
| `price_above_ema20` | Boolean |
| `price_above_ema50` | Boolean |
| `price_above_ema150` | Boolean |
| `price_above_ema200` | Boolean |
| `price_above_sma20` | Boolean |
| `price_above_sma50` | Boolean |
| `price_above_sma150` | Boolean |
| `price_above_sma200` | Boolean |
| `ema20_above_ema50` | Boolean — short-term trend confirmation |
| `ema50_above_ema150` | Boolean |
| `ema50_above_ema200` | Boolean — medium-term trend confirmation |
| `ema150_above_ema200` | Boolean — long-term trend alignment |
| `sma20_above_sma50` | Boolean |
| `sma50_above_sma150` | Boolean |
| `sma50_above_sma200` | Boolean |
| `sma150_above_sma200` | Boolean |

### Bollinger Bands (20-period, 2σ)

| Feature | Description |
|---|---|
| `bb_upper` | Upper band value |
| `bb_middle` | Middle band (SMA-20) value |
| `bb_lower` | Lower band value |
| `bb_pct_b` | %B — where price sits within the bands: 0 = lower band, 0.5 = midline, 1 = upper band |
| `bb_width_pct` | Band width as % of midline — measures volatility expansion/contraction |
| `price_above_bb_middle` | Boolean |
| `price_above_bb_upper` | Boolean — extended / overbought territory |
| `price_below_bb_lower` | Boolean — compressed / oversold territory |

### Momentum & Oscillators

| Feature | Description |
|---|---|
| `rsi` | RSI(14) — cross-validated against CSV values for prime tickers |
| `adx` | ADX(14) — trend strength |
| `roc20` | Rate of change over 20 days (%) |
| `macd_histogram` | MACD(12,26,9) histogram value |

### Volatility & Liquidity

| Feature | Description |
|---|---|
| `rv20` | 20-day realized volatility — IV proxy; `std(daily_returns, 20) * sqrt(252)` |
| `atr_pct` | ATR(14) as % of close — normalized range / spread proxy |
| `volume_ratio` | Today's volume ÷ 20-day avg volume |

### Positioning

| Feature | Description |
|---|---|
| `pct_from_52wk_high` | `(52wk_high - close) / 52wk_high * 100` — maps to "cushion" concept |
| `close_price` | Raw close |
| `volume` | Raw volume |
| `sector` | From `universe_fundamentals.sector` |

### Macro (per date, not per ticker)

Stored in `detective_macro`, joined at analysis time:

| Feature | Source |
|---|---|
| `vix_score` | `daily_signals` where `source='vix'` |
| `vix_direction` | `daily_signals` metadata |
| `market_posture` | `digests` table |
| `composite_score` | `digests` table |
| `fear_greed_score` | `daily_signals` where `source='fear_greed'` |
| `spy_above_ema50` | Computed from `universe_daily_ohlcv` for SPY |
| `spy_above_ema200` | Computed from `universe_daily_ohlcv` for SPY |
| `spy_rsi` | RSI(14) on SPY as of that date |
| `top_sectors` | JSON array — sector ETF leaders from `daily_signals` |

---

## Comparison Universe (per date)

For each of the ~60 unique dates in the CSV, the control group is built as:

1. All tickers in `universe_daily_ohlcv` with a row for that exact date
2. Filtered by `universe_fundamentals`: `market_cap_b >= 3.0` AND `price >= 5.0`
3. Minus the prime tickers for that specific date

Typical size: ~600–900 tickers per date. Total feature matrix: ~42,000 rows.
`build.py` skips already-computed `(date, ticker)` pairs so subsequent runs are fast.

---

## SQLite Schema

Two new tables in `market_intelligence.db`, created by `store.py`:

```sql
CREATE TABLE IF NOT EXISTS detective_features (
    date                    TEXT NOT NULL,
    ticker                  TEXT NOT NULL,
    is_prime                INTEGER NOT NULL,
    close_price             REAL,
    volume                  INTEGER,
    rsi                     REAL,
    adx                     REAL,
    ema20                   REAL,
    ema50                   REAL,
    ema150                  REAL,
    ema200                  REAL,
    sma20                   REAL,
    sma50                   REAL,
    sma150                  REAL,
    sma200                  REAL,
    price_vs_ema20_pct      REAL,
    price_vs_ema50_pct      REAL,
    price_vs_ema150_pct     REAL,
    price_vs_ema200_pct     REAL,
    price_vs_sma20_pct      REAL,
    price_vs_sma50_pct      REAL,
    price_vs_sma150_pct     REAL,
    price_vs_sma200_pct     REAL,
    price_above_ema20       INTEGER,
    price_above_ema50       INTEGER,
    price_above_ema150      INTEGER,
    price_above_ema200      INTEGER,
    price_above_sma20       INTEGER,
    price_above_sma50       INTEGER,
    price_above_sma150      INTEGER,
    price_above_sma200      INTEGER,
    ema20_above_ema50       INTEGER,
    ema50_above_ema150      INTEGER,
    ema50_above_ema200      INTEGER,
    ema150_above_ema200     INTEGER,
    sma20_above_sma50       INTEGER,
    sma50_above_sma150      INTEGER,
    sma50_above_sma200      INTEGER,
    sma150_above_sma200     INTEGER,
    bb_upper                REAL,
    bb_middle               REAL,
    bb_lower                REAL,
    bb_pct_b                REAL,
    bb_width_pct            REAL,
    price_above_bb_middle   INTEGER,
    price_above_bb_upper    INTEGER,
    price_below_bb_lower    INTEGER,
    rv20                    REAL,
    atr_pct                 REAL,
    volume_ratio            REAL,
    roc20                   REAL,
    macd_histogram          REAL,
    pct_from_52wk_high      REAL,
    sector                  TEXT,
    computed_at             TEXT NOT NULL,
    PRIMARY KEY (date, ticker)
);

CREATE TABLE IF NOT EXISTS detective_macro (
    date                TEXT PRIMARY KEY,
    vix_score           REAL,
    vix_direction       TEXT,
    market_posture      TEXT,
    composite_score     REAL,
    fear_greed_score    REAL,
    spy_above_ema50     INTEGER,
    spy_above_ema200    INTEGER,
    spy_rsi             REAL,
    top_sectors         TEXT
);
```

---

## Analysis Workflow

`analyze.py` runs three passes and writes results to `data/detective/`:

**Pass 1 — Feature ranking**
For each feature, compute the KS statistic between prime=1 and prime=0 distributions.
Rank all features by KS statistic descending. This surfaces which indicators the
algorithm actually uses — and which ones are noise.

**Pass 2 — Threshold discovery**
For the top 10 ranked features, grid-search threshold ranges.
Scoring: maximize precision subject to recall ≥ 70%.
Output: ranked list of criteria combinations with precision/recall breakdown.

**Pass 3 — Criteria output**
Produce 3–5 candidate criteria sets as JSON. For each, report:
- Overall precision and recall
- False positive rate broken down by sector
- Prime tickers missed and which criterion they fail
- Macro conditions on dates with most misses

Output files:
```
data/detective/
├── prime_tickers.csv          — the input CSV (source of truth)
├── analysis_2026-06-18.json   — feature rankings + candidate criteria
├── criteria_v1.json           — first criteria hypothesis to validate
└── sessions/                  — per-session findings notes (human-edited)
```

**`validate.py`** accepts a criteria JSON and reports against the full `detective_features`
table. Example criteria format:

```json
{
  "rsi_min": 42,
  "rsi_max": 68,
  "adx_min": 15,
  "price_above_ema50": true,
  "ema20_above_ema50": true,
  "rv20_min": 0.22,
  "vix_max": 25,
  "spy_above_ema50": true
}
```

---

## Iterative Session Strategy

This is a multi-session research project. Each session follows this pattern:

1. Run `build` if new OHLCV data has been added to the store since last session
2. Run `analyze` to get updated feature rankings and criteria candidates
3. Review `analysis_*.json` — identify the highest-KS features, look at the candidate
   criteria sets, note which prime tickers are consistently missed
4. Edit `criteria_vN.json` with a refined hypothesis
5. Run `validate` to score it — record precision/recall in `data/detective/sessions/`
6. Repeat

Once a criteria set achieves satisfactory precision on the holdout dates, wire it into
`src/screener/csp_scanner.py` as a pre-filter before the options screener runs.

---

## CSV Cross-Validation

The prime-state CSV includes RSI and ADX values recorded at the time of the scan.
During `build.py`, for every prime ticker, the computed RSI and ADX are compared
against the CSV values. Discrepancies > 5 points are logged as warnings.
This validates that our indicator computation matches the friend's scanner's methodology
before we trust any of the analysis.

---

## Verification

End-to-end test after implementation:

```bash
# 1. Place the CSV at data/detective/prime_tickers.csv

# 2. Build the feature matrix
docker compose run --rm pipeline python -m src.algo_detective.build
# Expected: ~42,000 rows in detective_features, ~60 rows in detective_macro
# Expected: RSI/ADX cross-validation warnings for <5% of prime tickers

# 3. Inspect a known date
docker compose run --rm pipeline python -m src.algo_detective.build --inspect 2025-10-07
# Expected: DELL, WPM, MS, HWM, GS, JPM, BAC, NTAP all show is_prime=1
# Expected: ~700 other tickers on that date show is_prime=0

# 4. Run analysis
docker compose run --rm pipeline python -m src.algo_detective.analyze
# Expected: data/detective/analysis_*.json created
# Expected: RSI, ADX, EMA position, macro posture appear in top-ranked features

# 5. Validate baseline criteria
docker compose run --rm pipeline python -m src.algo_detective.validate \
  --criteria '{"price_above_ema50": true, "rsi_min": 40, "rsi_max": 70}'
# Expected: precision and recall scores printed to stdout
```
