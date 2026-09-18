"""Session 07 — Communication Services false positive analysis for v24.

v24 has 182 FPs in Communication Services. IV is NOT useful (prime mean=0.422,
FP mean=0.414 — nearly identical). This session finds non-IV features that can
discriminate ComSvc TPs from FPs and tests gate candidates.

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session07_comsvc
"""

from __future__ import annotations

import logging
from collections import Counter

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria
from .store import _get_connection, get_all_features, get_options_index

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
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

KS_FEATURES = [
    "forward_pe", "beta", "market_cap_b", "adx", "bb_width_pct", "rv20",
    "roc20", "macd_histogram", "rsi", "price_vs_sma150_pct", "atr_pct",
    "best_iv", "pct_from_52wk_high", "price_vs_ema200_pct", "dividend_yield", "fcf",
]


def _load_sp500_with_options() -> list[dict]:
    features = get_all_features()
    conn = _get_connection()
    try:
        sp500 = {r["symbol"] for r in conn.execute(
            "SELECT symbol FROM universe_fundamentals WHERE universes LIKE '%sp500%'"
        ).fetchall()}
    finally:
        conn.close()
    filtered = [f for f in features if f["is_prime"] == 1 or f["ticker"] in sp500]
    options_idx = get_options_index()
    return [
        {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
        for f in filtered
    ]


def _fmtv(row: dict, feat: str, w: int = 7) -> str:
    v = row.get(feat)
    if v is None:
        return " " * w + "N/A"
    return f"{v:{w}.2f}"


def main() -> None:
    print("\n=== Session 07 — Communication Services FP Analysis (v24) ===\n")

    # ── Load data ─────────────────────────────────────────────────────────────
    rows = _load_sp500_with_options()
    prime_all = [r for r in rows if r["is_prime"] == 1]
    control_all = [r for r in rows if r["is_prime"] == 0]
    print(f"Dataset: {len(prime_all)} prime, {len(control_all)} control ({len(rows)} total)")

    # ── Apply v24 globally ────────────────────────────────────────────────────
    tp_all = [r for r in prime_all if _apply_criteria(r, V24)]
    fp_all = [r for r in control_all if _apply_criteria(r, V24)]
    prec = len(tp_all) / (len(tp_all) + len(fp_all)) if (tp_all or fp_all) else 0
    rec = len(tp_all) / len(prime_all) if prime_all else 0
    print(f"\nv24 global:  P={prec:.1%}  R={rec:.1%}  TP={len(tp_all)}  FP={len(fp_all)}")

    # ── Isolate Communication Services ────────────────────────────────────────
    comsvc_tp = [r for r in tp_all if r.get("sector") == "Communication Services"]
    comsvc_fp = [r for r in fp_all if r.get("sector") == "Communication Services"]
    print(f"\nComSvc TPs: {len(comsvc_tp)}  |  ComSvc FPs: {len(comsvc_fp)}")

    # ── TP ticker breakdown ────────────────────────────────────────────────────
    print("\n--- ComSvc True Positives (all) ---")
    print(f"  {'date':<12} {'ticker':<8} {'mcap_b':>8} {'iv':>7} {'adx':>6} {'rv20':>6} {'roc20':>7} {'pe':>7} {'bb_w':>7} {'pct_hi':>8}")
    for r in sorted(comsvc_tp, key=lambda x: (x["ticker"], x["date"])):
        print(
            f"  {r['date']:<12} {r['ticker']:<8}"
            f" {_fmtv(r,'market_cap_b'):>8}"
            f" {_fmtv(r,'best_iv'):>7}"
            f" {_fmtv(r,'adx'):>6}"
            f" {_fmtv(r,'rv20'):>6}"
            f" {_fmtv(r,'roc20'):>7}"
            f" {_fmtv(r,'forward_pe'):>7}"
            f" {_fmtv(r,'bb_width_pct'):>7}"
            f" {_fmtv(r,'pct_from_52wk_high'):>8}"
        )

    # ── FP ticker frequency ───────────────────────────────────────────────────
    print("\n--- Top 15 ComSvc FP tickers by frequency ---")
    fp_ticker_counts = Counter(r["ticker"] for r in comsvc_fp)
    top15 = fp_ticker_counts.most_common(15)
    print(f"  {'ticker':<8} {'count':>6}  {'mcap_b':>8} {'iv':>7} {'adx':>6} {'rv20':>6} {'roc20':>7} {'pe':>7}")
    for ticker, cnt in top15:
        sample = next(r for r in comsvc_fp if r["ticker"] == ticker)
        print(
            f"  {ticker:<8} {cnt:>6}"
            f"  {_fmtv(sample,'market_cap_b'):>8}"
            f" {_fmtv(sample,'best_iv'):>7}"
            f" {_fmtv(sample,'adx'):>6}"
            f" {_fmtv(sample,'rv20'):>6}"
            f" {_fmtv(sample,'roc20'):>7}"
            f" {_fmtv(sample,'forward_pe'):>7}"
        )

    # ── KS statistics: ComSvc TP vs FP ────────────────────────────────────────
    print("\n--- KS statistics: ComSvc TP vs FP distributions ---")
    print(f"  {'feature':<28} {'KS':>6}  {'tp_mean':>9}  {'fp_mean':>9}  {'direction'}")
    ks_results = []
    for feat in KS_FEATURES:
        tp_vals = [r[feat] for r in comsvc_tp if r.get(feat) is not None]
        fp_vals = [r[feat] for r in comsvc_fp if r.get(feat) is not None]
        if len(tp_vals) < 3 or len(fp_vals) < 3:
            ks_results.append((feat, 0.0, None, None, "insufficient data"))
            continue
        stat, _ = ks_2samp(tp_vals, fp_vals)
        tp_mean = float(np.mean(tp_vals))
        fp_mean = float(np.mean(fp_vals))
        direction = "TP>" if tp_mean > fp_mean else "FP>"
        ks_results.append((feat, round(stat, 4), round(tp_mean, 3), round(fp_mean, 3), direction))

    for feat, stat, tp_mean, fp_mean, direction in sorted(ks_results, key=lambda x: x[1], reverse=True):
        if tp_mean is None:
            print(f"  {feat:<28} {'N/A':>6}  {'N/A':>9}  {'N/A':>9}  {direction}")
        else:
            print(f"  {feat:<28} {stat:>6.3f}  {tp_mean:>9.3f}  {fp_mean:>9.3f}  {direction}")

    # ── Gate candidates: global impact ────────────────────────────────────────
    print("\n--- Gate candidate tests (global impact across ALL sectors) ---")

    def _score(criteria: dict) -> dict:
        tp = sum(1 for r in prime_all if _apply_criteria(r, criteria))
        fp = sum(1 for r in control_all if _apply_criteria(r, criteria))
        p = tp / (tp + fp) if (tp + fp) else 0
        r = tp / len(prime_all) if prime_all else 0
        return {"P": round(p, 4), "R": round(r, 4), "TP": tp, "FP": fp}

    # Baseline v24 score
    base = _score(V24)
    print(f"\n  Baseline v24: P={base['P']:.1%}  R={base['R']:.1%}  TP={base['TP']}  FP={base['FP']}")

    # forward_pe_max
    print("\n  forward_pe_max:")
    for threshold in [25, 30, 35, 40, 50]:
        crit = {**V24, "forward_pe_max": threshold}
        s = _score(crit)
        delta_tp = s["TP"] - base["TP"]
        delta_fp = s["FP"] - base["FP"]
        print(f"    forward_pe_max={threshold:<3}: P={s['P']:.1%}  R={s['R']:.1%}  TP={s['TP']} ({delta_tp:+d})  FP={s['FP']} ({delta_fp:+d})")

    # market_cap_b_min (global)
    print("\n  market_cap_b_min (global, already at 25 — testing tighter):")
    for threshold in [30, 40, 50]:
        crit = {**V24, "market_cap_b_min": threshold}
        s = _score(crit)
        delta_tp = s["TP"] - base["TP"]
        delta_fp = s["FP"] - base["FP"]
        print(f"    market_cap_b_min={threshold:<3}: P={s['P']:.1%}  R={s['R']:.1%}  TP={s['TP']} ({delta_tp:+d})  FP={s['FP']} ({delta_fp:+d})")

    # adx_min (currently 15, testing tighter)
    print("\n  adx_min (currently 15):")
    for threshold in [18, 20, 22]:
        crit = {**V24, "adx_min": threshold}
        s = _score(crit)
        delta_tp = s["TP"] - base["TP"]
        delta_fp = s["FP"] - base["FP"]
        print(f"    adx_min={threshold:<2}: P={s['P']:.1%}  R={s['R']:.1%}  TP={s['TP']} ({delta_tp:+d})  FP={s['FP']} ({delta_fp:+d})")

    # rv20_max (currently 0.45)
    print("\n  rv20_max (currently 0.45):")
    for threshold in [0.40, 0.42]:
        crit = {**V24, "rv20_max": threshold}
        s = _score(crit)
        delta_tp = s["TP"] - base["TP"]
        delta_fp = s["FP"] - base["FP"]
        print(f"    rv20_max={threshold:.2f}: P={s['P']:.1%}  R={s['R']:.1%}  TP={s['TP']} ({delta_tp:+d})  FP={s['FP']} ({delta_fp:+d})")

    # roc20_min
    print("\n  roc20_min (new gate):")
    for threshold in [0, 2, 5]:
        crit = {**V24, "roc20_min": threshold}
        s = _score(crit)
        delta_tp = s["TP"] - base["TP"]
        delta_fp = s["FP"] - base["FP"]
        print(f"    roc20_min={threshold:<2}: P={s['P']:.1%}  R={s['R']:.1%}  TP={s['TP']} ({delta_tp:+d})  FP={s['FP']} ({delta_fp:+d})")

    # ── ComSvc-scoped market cap floor (in-memory, not added to _apply_criteria) ──
    print("\n--- ComSvc-scoped market_cap_b floor (in-memory filter) ---")
    print("  (simulates a communication_services_market_cap_b_min gate)")
    for mcap_floor in [25, 50, 75, 100]:
        cs_fp_cut = [f for f in comsvc_fp if f.get("market_cap_b", 0) >= mcap_floor]
        cs_tp_keep = [f for f in comsvc_tp if f.get("market_cap_b", 0) >= mcap_floor]
        fp_remaining_global = (base["FP"] - len(comsvc_fp)) + len(cs_fp_cut)
        tp_remaining_global = (base["TP"] - len(comsvc_tp)) + len(cs_tp_keep)
        p_new = tp_remaining_global / (tp_remaining_global + fp_remaining_global) if (tp_remaining_global + fp_remaining_global) else 0
        r_new = tp_remaining_global / len(prime_all) if prime_all else 0
        print(f"  comsvc_mcap_min={mcap_floor:<3}: keeps {len(cs_tp_keep)}/{len(comsvc_tp)} TPs, cuts to {len(cs_fp_cut)}/{len(comsvc_fp)} FPs  |  global est. P={p_new:.1%}  R={r_new:.1%}  TP={tp_remaining_global}  FP={fp_remaining_global}")

    # ── FP mcap distribution ───────────────────────────────────────────────────
    print("\n--- ComSvc FP market_cap_b distribution ---")
    fp_mcaps = sorted([r.get("market_cap_b", 0) or 0 for r in comsvc_fp])
    tp_mcaps = sorted([r.get("market_cap_b", 0) or 0 for r in comsvc_tp])
    percentiles = [10, 25, 50, 75, 90]
    print(f"  {'pct':<6} {'FP_mcap':>10}  {'TP_mcap':>10}")
    for p in percentiles:
        fp_p = float(np.percentile(fp_mcaps, p)) if fp_mcaps else float("nan")
        tp_p = float(np.percentile(tp_mcaps, p)) if tp_mcaps else float("nan")
        print(f"  p{p:<5} {fp_p:>10.1f}  {tp_p:>10.1f}")

    print(f"\n  FP  mean mcap={float(np.mean(fp_mcaps)):.1f}B  median={float(np.median(fp_mcaps)):.1f}B")
    print(f"  TP  mean mcap={float(np.mean(tp_mcaps)):.1f}B  median={float(np.median(tp_mcaps)):.1f}B")

    # ── FP breakdown by mcap bucket ────────────────────────────────────────────
    print("\n--- ComSvc FP count by mcap bucket ---")
    buckets = [(0, 25), (25, 50), (50, 100), (100, 200), (200, 999999)]
    for lo, hi in buckets:
        n = sum(1 for r in comsvc_fp if lo <= (r.get("market_cap_b") or 0) < hi)
        label = f"[{lo},{hi if hi < 999999 else '∞'})"
        print(f"  {label:<15}: {n} FPs")

    # ── Unique prime ComSvc tickers ───────────────────────────────────────────
    print("\n--- Unique prime ComSvc tickers in dataset (passing v24) ---")
    tp_tickers = sorted(set(r["ticker"] for r in comsvc_tp))
    print(f"  {tp_tickers}")

    print("\n=== Session 07 complete ===\n")


if __name__ == "__main__":
    main()
