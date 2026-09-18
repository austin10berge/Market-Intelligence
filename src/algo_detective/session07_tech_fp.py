"""Session 07 — Technology sector false-positive analysis for v24 rule set.

Loads SP500 + prime rows (with best_iv joined from detective_options),
applies v24 criteria, isolates Technology TPs and FPs, then:
  1. Compares feature distributions (KS statistic) to find best discriminators.
  2. Grid-searches gate candidates and measures global precision/recall impact.
  3. Reports top FP tickers and TP tickers.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session07_tech_fp
"""

from __future__ import annotations

import logging
from collections import Counter

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria
from .store import _get_connection, get_all_features, get_options_index

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)

V24 = {
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
}

# ── Load data ─────────────────────────────────────────────────────────────────

logger.info("Loading features...")
all_features = get_all_features()

conn = _get_connection()
try:
    sp500 = {
        r["symbol"]
        for r in conn.execute(
            "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
        ).fetchall()
    }
finally:
    conn.close()

# Keep primes (all) + SP500 controls
features = [f for f in all_features if f["is_prime"] == 1 or f["ticker"] in sp500]

# Join best_iv from options index
options_idx = get_options_index()
features = [
    {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
    for f in features
]

logger.info(
    "Dataset: %d rows (%d prime, %d control)",
    len(features),
    sum(1 for f in features if f["is_prime"] == 1),
    sum(1 for f in features if f["is_prime"] == 0),
)

prime = [f for f in features if f["is_prime"] == 1]
control = [f for f in features if f["is_prime"] == 0]

# ── v24 passing rows ──────────────────────────────────────────────────────────

v24_prime = [f for f in prime if _apply_criteria(f, V24)]  # TPs
v24_control = [f for f in control if _apply_criteria(f, V24)]  # FPs

baseline_tp = len(v24_prime)
baseline_fp = len(v24_control)
baseline_prec = baseline_tp / (baseline_tp + baseline_fp) if (baseline_tp + baseline_fp) > 0 else 0
baseline_recall = baseline_tp / len(prime) if prime else 0

print("\n=== v24 baseline ===")
print(
    f"  TP={baseline_tp}  FP={baseline_fp}  "
    f"P={baseline_prec:.1%}  R={baseline_recall:.1%}"
)

# ── Isolate Technology ────────────────────────────────────────────────────────

tech_tp = [f for f in v24_prime if f.get("sector") == "Technology"]
tech_fp = [f for f in v24_control if f.get("sector") == "Technology"]

print(f"\nTechnology TPs: {len(tech_tp)}, FPs: {len(tech_fp)}")

# ── Feature distribution comparison ──────────────────────────────────────────

FEATS = [
    "forward_pe",
    "beta",
    "market_cap_b",
    "adx",
    "bb_width_pct",
    "rv20",
    "roc20",
    "macd_histogram",
    "rsi",
    "price_vs_sma150_pct",
    "atr_pct",
    "best_iv",
    "pct_from_52wk_high",
    "price_vs_ema200_pct",
    "volume_ratio",
    "revenue_growth",
    "earnings_growth",
    "peg_ratio",
    "debt_to_equity",
    "fcf",
]

print("\n=== Feature distributions: Tech TPs vs Tech FPs ===")
feat_stats = []
for feat in FEATS:
    tp_vals = [f[feat] for f in tech_tp if f.get(feat) is not None]
    fp_vals = [f[feat] for f in tech_fp if f.get(feat) is not None]
    if len(tp_vals) < 3 or len(fp_vals) < 3:
        print(f"  {feat:<30} -- insufficient data (TP n={len(tp_vals)}, FP n={len(fp_vals)})")
        continue
    ks, pval = ks_2samp(tp_vals, fp_vals)
    tp_med = np.median(tp_vals)
    fp_med = np.median(fp_vals)
    tp_p10 = np.percentile(tp_vals, 10)
    tp_p90 = np.percentile(tp_vals, 90)
    feat_stats.append((ks, feat, tp_med, fp_med, tp_p10, tp_p90, pval))

feat_stats.sort(reverse=True)
for ks, feat, tp_med, fp_med, tp_p10, tp_p90, pval in feat_stats:
    print(
        f"  {feat:<30} KS={ks:.3f}  TP_med={tp_med:+.3f}  FP_med={fp_med:+.3f}  "
        f"TP_p10={tp_p10:+.3f}  TP_p90={tp_p90:+.3f}  p={pval:.3e}"
    )

# ── Gate testing: global impact across all sectors ────────────────────────────

print("\n=== Gate candidates (global impact on all FPs/TPs) ===")
print(
    f"  {'gate':<30} {'TP':>5} {'FP':>6} {'P':>7} {'R':>7} "
    f"{'tech_tp':>8} {'tech_fp':>8} {'dFP':>7} {'dTP':>6}"
)
print("  " + "-" * 88)

candidates = [
    ("forward_pe_max", [25, 30, 35, 40, 50, 60]),
    ("market_cap_b_min", [50, 75, 100, 150]),
    ("rv20_max", [0.30, 0.35, 0.40]),
    ("adx_min", [18, 20, 22, 25]),
    ("bb_width_pct_min", [5, 6, 7, 8]),
    ("rsi_min", [40, 45, 50, 55]),
    ("roc20_min", [0, 2, 5]),
    ("pct_from_52wk_high_max", [10, 12, 15]),
    ("price_vs_ema200_pct_min", [2, 5, 8]),
    ("price_vs_ema200_pct_max", [30, 35]),
    ("technology_iv_min", [0.25, 0.30, 0.35, 0.40]),
    ("technology_market_cap_b_min", [50, 75, 100]),
]

for key, vals in candidates:
    # Skip technology_market_cap_b_min — not a recognized special key in _apply_criteria
    # Instead we'll test it via sector-filtered direct checks below
    if key == "technology_market_cap_b_min":
        continue
    for v in vals:
        test_crit = {**V24, key: v}
        tp_new = sum(1 for f in prime if _apply_criteria(f, test_crit))
        fp_new = sum(1 for f in control if _apply_criteria(f, test_crit))
        tp_tech = sum(1 for f in tech_tp if _apply_criteria(f, test_crit))
        fp_tech = sum(1 for f in tech_fp if _apply_criteria(f, test_crit))
        prec = tp_new / (tp_new + fp_new) if (tp_new + fp_new) > 0 else 0
        recall = tp_new / len(prime) if prime else 0
        d_fp = fp_new - baseline_fp
        d_tp = tp_new - baseline_tp
        print(
            f"  {key}={v:<22} {tp_new:>5} {fp_new:>6} {prec:>7.1%} {recall:>7.1%} "
            f"{tp_tech:>4}/{len(tech_tp):<3} {fp_tech:>4}/{len(tech_fp):<3} "
            f"{d_fp:>+7} {d_tp:>+6}"
        )

# ── Technology-specific market-cap gate (manual filter since it's not a built-in key) ──

print("\n=== Tech-only market_cap_b gate (manual sector filter) ===")
print(
    f"  {'gate':<30} {'TP':>5} {'FP':>6} {'P':>7} {'R':>7} "
    f"{'tech_tp':>8} {'tech_fp':>8} {'dFP':>7} {'dTP':>6}"
)
print("  " + "-" * 88)

for mcap_floor in [50, 75, 100, 150, 200]:

    def tech_mcap_filter(f: dict, floor: float = mcap_floor) -> bool:
        if f.get("sector") == "Technology":
            mc = f.get("market_cap_b")
            if mc is None or mc < floor:
                return False
        return _apply_criteria(f, V24)

    tp_new = sum(1 for f in prime if tech_mcap_filter(f))
    fp_new = sum(1 for f in control if tech_mcap_filter(f))
    tp_tech = sum(1 for f in tech_tp if f.get("market_cap_b") is not None and f["market_cap_b"] >= mcap_floor)
    fp_tech = sum(1 for f in tech_fp if f.get("market_cap_b") is not None and f["market_cap_b"] >= mcap_floor)
    prec = tp_new / (tp_new + fp_new) if (tp_new + fp_new) > 0 else 0
    recall = tp_new / len(prime) if prime else 0
    d_fp = fp_new - baseline_fp
    d_tp = tp_new - baseline_tp
    label = f"technology_mcap_b>={mcap_floor}B"
    print(
        f"  {label:<30} {tp_new:>5} {fp_new:>6} {prec:>7.1%} {recall:>7.1%} "
        f"{tp_tech:>4}/{len(tech_tp):<3} {fp_tech:>4}/{len(tech_fp):<3} "
        f"{d_fp:>+7} {d_tp:>+6}"
    )

# ── Combination gates ─────────────────────────────────────────────────────────

print("\n=== Combination gate candidates (tech-specific) ===")

combos = [
    ("technology_iv_min=0.30 + adx_min=20", {**V24, "technology_iv_min": 0.30, "adx_min": 20}),
    ("technology_iv_min=0.30 + roc20_min=2", {**V24, "technology_iv_min": 0.30, "roc20_min": 2}),
    ("technology_iv_min=0.35", {**V24, "technology_iv_min": 0.35}),
    ("technology_iv_min=0.30", {**V24, "technology_iv_min": 0.30}),
    ("adx_min=20 + roc20_min=2", {**V24, "adx_min": 20, "roc20_min": 2}),
    ("adx_min=22 + roc20_min=2", {**V24, "adx_min": 22, "roc20_min": 2}),
    ("forward_pe_max=40 + technology_iv_min=0.30", {**V24, "forward_pe_max": 40, "technology_iv_min": 0.30}),
    ("roc20_min=5 + technology_iv_min=0.30", {**V24, "roc20_min": 5, "technology_iv_min": 0.30}),
]
print(
    f"  {'gate':<45} {'TP':>5} {'FP':>6} {'P':>7} {'R':>7} "
    f"{'tech_tp':>8} {'tech_fp':>8} {'dFP':>7} {'dTP':>6}"
)
print("  " + "-" * 100)
for label, crit in combos:
    tp_new = sum(1 for f in prime if _apply_criteria(f, crit))
    fp_new = sum(1 for f in control if _apply_criteria(f, crit))
    tp_tech = sum(1 for f in tech_tp if _apply_criteria(f, crit))
    fp_tech = sum(1 for f in tech_fp if _apply_criteria(f, crit))
    prec = tp_new / (tp_new + fp_new) if (tp_new + fp_new) > 0 else 0
    recall = tp_new / len(prime) if prime else 0
    d_fp = fp_new - baseline_fp
    d_tp = tp_new - baseline_tp
    print(
        f"  {label:<45} {tp_new:>5} {fp_new:>6} {prec:>7.1%} {recall:>7.1%} "
        f"{tp_tech:>4}/{len(tech_tp):<3} {fp_tech:>4}/{len(tech_fp):<3} "
        f"{d_fp:>+7} {d_tp:>+6}"
    )

# ── Top FP tickers ────────────────────────────────────────────────────────────

fp_tickers = Counter(f.get("ticker") for f in tech_fp)
print("\n=== Top 20 Tech FP tickers ===")
for ticker, cnt in fp_tickers.most_common(20):
    sample = next(f for f in tech_fp if f.get("ticker") == ticker)
    mcap = sample.get("market_cap_b")
    adx = sample.get("adx")
    fpe = sample.get("forward_pe")
    iv = sample.get("best_iv")
    roc = sample.get("roc20")
    print(
        f"  {ticker:<10} x{cnt:2}  "
        f"mcap={mcap:.1f}B  " if mcap is not None else f"  {ticker:<10} x{cnt:2}  mcap=N/A  ",
        end="",
    )
    print(
        f"adx={adx:.1f}  " if adx is not None else "adx=N/A  ",
        end="",
    )
    print(
        f"iv={iv:.3f}  " if iv is not None else "iv=N/A  ",
        end="",
    )
    print(
        f"fwd_pe={fpe:.1f}  " if fpe is not None else "fwd_pe=N/A  ",
        end="",
    )
    print(f"roc20={roc:.1f}" if roc is not None else "roc20=N/A")

# ── TP ticker list ────────────────────────────────────────────────────────────

print("\n=== Tech TP tickers ===")
tp_tickers = Counter(f.get("ticker") for f in tech_tp)
for ticker, cnt in tp_tickers.most_common():
    sample = next(f for f in tech_tp if f.get("ticker") == ticker)
    mcap = sample.get("market_cap_b")
    iv = sample.get("best_iv")
    roc = sample.get("roc20")
    fpe = sample.get("forward_pe")
    print(
        f"  {ticker:<10} x{cnt:2}  "
        f"mcap={mcap:.1f}B  " if mcap is not None else f"  {ticker:<10} x{cnt:2}  mcap=N/A  ",
        end="",
    )
    print(
        f"iv={iv:.3f}  " if iv is not None else "iv=N/A  ",
        end="",
    )
    print(
        f"fwd_pe={fpe:.1f}  " if fpe is not None else "fwd_pe=N/A  ",
        end="",
    )
    print(f"roc20={roc:.1f}" if roc is not None else "roc20=N/A")

# ── Summary of sector FP breakdown ───────────────────────────────────────────

print("\n=== FP sector breakdown (v24) ===")
sector_fps = Counter(f.get("sector") for f in v24_control)
for sector, cnt in sector_fps.most_common():
    pct = cnt / baseline_fp * 100
    print(f"  {sector or 'Unknown':<25} {cnt:>5}  ({pct:.1f}%)")


if __name__ == "__main__":
    pass
