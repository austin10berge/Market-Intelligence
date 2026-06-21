"""Session 13 — Consumer Cyclical RSI gate + Technology RSI gate.

Session 12 found Consumer Cyclical RSI is the strongest unused discriminator:
  CC TP_med=46.1 vs FP_med=51.5, KS=0.462

Session 12 also identified:
  Tech RSI: TP_med=52.9 vs FP_med=54.3, KS=0.228 (weaker but ~60 FPs to cut)
  ComSvc RSI: inverted — TPs have HIGHER RSI (TP_med=48.8 vs FP_med=45.9)

Goals:
  1. CC deep-dive: which tickers / what days; RSI sweep for consumer_cyclical_rsi_max
  2. Tech RSI sweep: technology_rsi_max (weaker signal, may cost recall)
  3. Combine best CC+Tech RSI gates with v29 → v31 definition
  4. Report updated Pareto frontier

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session13
"""

from __future__ import annotations

from collections import Counter

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria, _score_criteria
from .store import _get_connection, get_all_features

# ── v29 (current best narrow-universe criteria) ───────────────────────────────

V29 = {
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
}


def _join_options(features: list[dict]) -> list[dict]:
    """Enrich with best_iv + pcr_vol from detective_options."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT date, ticker, best_iv, pcr_vol, pcr_oi FROM detective_options"
        ).fetchall()
        index = {(r["date"], r["ticker"]): dict(r) for r in rows}
    finally:
        conn.close()
    return [
        {
            **f,
            "best_iv": index.get((f["date"], f["ticker"]), {}).get("best_iv"),
            "pcr_vol": index.get((f["date"], f["ticker"]), {}).get("pcr_vol"),
            "pcr_oi": index.get((f["date"], f["ticker"]), {}).get("pcr_oi"),
        }
        for f in features
    ]


def _ks(a: list[float], b: list[float]) -> tuple[float, float]:
    if len(a) < 5 or len(b) < 5:
        return 0.0, 1.0
    stat, pval = ks_2samp(a, b)
    return round(float(stat), 4), float(pval)


def _pct(vals: list[float], p: int) -> float:
    return round(float(np.percentile(vals, p)), 4) if vals else float("nan")


def _print_sweep(
    label: str,
    base: dict,
    narrow: list[dict],
    prime: list[dict],
    key: str,
    thresholds: list[float],
    base_p: float,
) -> list[dict]:
    print(f"\n=== {label} ===")
    print(f"  {'Gate':<35} {'P':>7} {'R':>7} {'TP':>5} {'FP':>5}  ΔP")
    results = []
    for thr in thresholds:
        crit = {**base, key: thr}
        res = _score_criteria(narrow, crit)
        delta_p = (res["precision"] - base_p) * 100
        print(
            f"  {key}={thr:<30.1f} {res['precision'] * 100:6.1f}%  {res['recall'] * 100:6.1f}%  "
            f"{res['true_positives']:4d}  {res['false_positives']:4d}  {delta_p:+.1f}pp"
        )
        results.append({**res, "threshold": thr})
    return results


def main() -> None:
    all_features = get_all_features()
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_options(narrow)

    prime = [f for f in narrow if f["is_prime"] == 1]
    ctrl = [f for f in narrow if f["is_prime"] == 0]
    n_dates = len(set(f["date"] for f in prime))

    print(
        f"\n=== Narrow universe: {len(prime)} prime, {len(ctrl)} control"
        f" ({len(prime_tickers)} tickers, {n_dates} dates) ==="
    )

    # ── v29 baseline ──────────────────────────────────────────────────────────
    v29_tp = [f for f in prime if _apply_criteria(f, V29)]
    v29_fp = [f for f in ctrl if _apply_criteria(f, V29)]
    base_p = len(v29_tp) / (len(v29_tp) + len(v29_fp)) if (v29_tp or v29_fp) else 0.0
    base_r = len(v29_tp) / len(prime)
    print(
        f"\nv29 baseline: P={base_p * 100:.1f}%  R={base_r * 100:.1f}%"
        f"  TP={len(v29_tp)}  FP={len(v29_fp)}"
    )

    # ── SECTION 1: Consumer Cyclical deep-dive ────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 1: Consumer Cyclical RSI Deep-Dive")
    print("=" * 70)

    cc_tp = [f for f in v29_tp if f.get("sector") == "Consumer Cyclical"]
    cc_fp = [f for f in v29_fp if f.get("sector") == "Consumer Cyclical"]
    print(f"\nCC in v29 survivors: {len(cc_tp)} TP, {len(cc_fp)} FP")

    if cc_tp and cc_fp:
        # Which CC tickers appear as TP vs FP
        tp_tickers = Counter(f["ticker"] for f in cc_tp)
        fp_tickers = Counter(f["ticker"] for f in cc_fp)
        print("\n  CC TP tickers:")
        for t, n in tp_tickers.most_common(15):
            print(f"    {t:<8} x{n}")
        print("\n  CC FP tickers (top 15):")
        for t, n in fp_tickers.most_common(15):
            print(f"    {t:<8} x{n}")

        # Feature distributions for CC TP vs FP
        cc_features_to_check = [
            "rsi",
            "adx",
            "rv20",
            "volume_ratio",
            "bb_width_pct",
            "bb_pct_b",
            "roc20",
            "macd_histogram",
            "pct_from_52wk_high",
            "price_vs_ema200_pct",
        ]
        print("\n  Feature distributions (CC v29 TPs vs FPs):")
        print(f"  {'Feature':<28} {'TP_med':>8} {'FP_med':>8} {'KS':>8}")
        print(f"  {'-' * 60}")
        for feat in cc_features_to_check:
            tp_vals = [f[feat] for f in cc_tp if f.get(feat) is not None]
            fp_vals = [f[feat] for f in cc_fp if f.get(feat) is not None]
            if len(tp_vals) < 3 or len(fp_vals) < 3:
                continue
            ks, _ = _ks(tp_vals, fp_vals)
            tp_med = _pct(tp_vals, 50)
            fp_med = _pct(fp_vals, 50)
            print(f"  {feat:<28} {tp_med:>8.3f} {fp_med:>8.3f} {ks:>8.3f}")

    # ── consumer_cyclical_rsi_max sweep ───────────────────────────────────────
    cc_rsi_thresholds = [40, 42, 44, 46, 48, 50, 52, 54, 56, 58, 60]
    _print_sweep(
        "consumer_cyclical_rsi_max sweep (v29 base)",
        V29,
        narrow,
        prime,
        "consumer_cyclical_rsi_max",
        cc_rsi_thresholds,
        base_p,
    )

    # ── SECTION 2: Technology RSI deep-dive ───────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 2: Technology RSI Deep-Dive")
    print("=" * 70)

    tech_tp = [f for f in v29_tp if f.get("sector") == "Technology"]
    tech_fp = [f for f in v29_fp if f.get("sector") == "Technology"]
    print(f"\nTech in v29 survivors: {len(tech_tp)} TP, {len(tech_fp)} FP")

    if tech_tp and tech_fp:
        tp_tickers = Counter(f["ticker"] for f in tech_tp)
        fp_tickers = Counter(f["ticker"] for f in tech_fp)
        print("\n  Tech TP tickers:")
        for t, n in tp_tickers.most_common(15):
            print(f"    {t:<8} x{n}")
        print("\n  Tech FP tickers (top 15):")
        for t, n in fp_tickers.most_common(15):
            print(f"    {t:<8} x{n}")

        tech_features = [
            "rsi",
            "adx",
            "rv20",
            "volume_ratio",
            "bb_width_pct",
            "pct_from_52wk_high",
            "macd_histogram",
            "market_cap_b",
            "price_vs_ema200_pct",
        ]
        print("\n  Feature distributions (Tech v29 TPs vs FPs):")
        print(f"  {'Feature':<28} {'TP_med':>8} {'FP_med':>8} {'KS':>8}")
        print(f"  {'-' * 60}")
        for feat in tech_features:
            tp_vals = [f[feat] for f in tech_tp if f.get(feat) is not None]
            fp_vals = [f[feat] for f in tech_fp if f.get(feat) is not None]
            if len(tp_vals) < 3 or len(fp_vals) < 3:
                continue
            ks, _ = _ks(tp_vals, fp_vals)
            tp_med = _pct(tp_vals, 50)
            fp_med = _pct(fp_vals, 50)
            print(f"  {feat:<28} {tp_med:>8.3f} {fp_med:>8.3f} {ks:>8.3f}")

    # ── technology_rsi_max sweep ──────────────────────────────────────────────
    tech_rsi_thresholds = [48, 50, 52, 54, 56, 58, 60, 65]
    _print_sweep(
        "technology_rsi_max sweep (v29 base)",
        V29,
        narrow,
        prime,
        "technology_rsi_max",
        tech_rsi_thresholds,
        base_p,
    )

    # ── SECTION 3: Combine CC + Tech RSI gates ────────────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 3: Combined CC RSI + Tech RSI gate sweep")
    print("=" * 70)
    print(f"\n  {'Criteria':<55} {'P':>7} {'R':>7} {'TP':>5} {'FP':>5}  ΔP")

    # Key combinations — CC gates from 44-52, Tech from 52-60
    candidates = []
    for cc_rsi in [44, 46, 48, 50, 52, 54]:
        for tech_rsi in [52, 54, 56, 58, 60]:
            crit = {**V29, "consumer_cyclical_rsi_max": cc_rsi, "technology_rsi_max": tech_rsi}
            res = _score_criteria(narrow, crit)
            candidates.append({**res, "cc_rsi": cc_rsi, "tech_rsi": tech_rsi})

    # Print all combinations that have recall >= 35%
    for c in sorted(candidates, key=lambda x: (x["precision"], x["recall"]), reverse=True):
        if c["recall"] >= 0.35:
            delta_p = (c["precision"] - base_p) * 100
            label = f"cc_rsi_max={c['cc_rsi']} + tech_rsi_max={c['tech_rsi']}"
            print(
                f"  {label:<55} {c['precision'] * 100:6.1f}%  {c['recall'] * 100:6.1f}%  "
                f"{c['true_positives']:4d}  {c['false_positives']:4d}  {delta_p:+.1f}pp"
            )

    # ── SECTION 4: All sector RSI breakdown within v29 ────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 4: All-sector RSI breakdown (v29 survivors)")
    print("=" * 70)
    hdr = f"{'Sector':<30} {'TP_n':>5} {'TP_med':>8} {'FP_n':>5} {'FP_med':>8} {'KS':>8} Direction"
    print(f"\n  {hdr}")
    print(f"  {'-' * 80}")

    sectors = sorted({f.get("sector") for f in narrow if f.get("sector")})
    for sector in sectors:
        tp_rsi = [
            f["rsi"] for f in v29_tp if f.get("sector") == sector and f.get("rsi") is not None
        ]
        fp_rsi = [
            f["rsi"] for f in v29_fp if f.get("sector") == sector and f.get("rsi") is not None
        ]
        if len(tp_rsi) < 3 or len(fp_rsi) < 3:
            continue
        ks, _ = _ks(tp_rsi, fp_rsi)
        tp_med = _pct(tp_rsi, 50)
        fp_med = _pct(fp_rsi, 50)
        direction = (
            "TP<FP (RSI cap useful)" if tp_med < fp_med else "TP>FP (inverted — RSI floor useful)"
        )
        print(
            f"  {sector:<30} {len(tp_rsi):>5} {tp_med:>8.1f}"
            f" {len(fp_rsi):>5} {fp_med:>8.1f} {ks:>8.3f}  {direction}"
        )

    # ── SECTION 5: v31 definition — best combined criteria ────────────────────
    print("\n" + "=" * 70)
    print("SECTION 5: v31 candidates (v29 + CC RSI + Tech RSI, recall >= 40%)")
    print("=" * 70)
    print(f"\n  {'Criteria':<55} {'P':>7} {'R':>7} {'TP':>5} {'FP':>5}  ΔP")

    v31_candidates = []

    # CC RSI alone
    for cc_rsi in [44, 46, 48, 50, 52]:
        crit = {**V29, "consumer_cyclical_rsi_max": cc_rsi}
        res = _score_criteria(narrow, crit)
        v31_candidates.append({**res, "label": f"v29 + cc_rsi_max={cc_rsi}"})

    # Tech RSI alone
    for tech_rsi in [52, 54, 56, 58]:
        crit = {**V29, "technology_rsi_max": tech_rsi}
        res = _score_criteria(narrow, crit)
        v31_candidates.append({**res, "label": f"v29 + tech_rsi_max={tech_rsi}"})

    # Best combined
    for c in candidates:
        v31_candidates.append(
            {**c, "label": f"v29 + cc_rsi_max={c['cc_rsi']} + tech_rsi_max={c['tech_rsi']}"}
        )

    # Also test with pcr_vol_max=2.0 added (v30c as base)
    v30c = {**V29, "pcr_vol_max": 2.0}
    for cc_rsi in [46, 50, 52]:
        crit = {**v30c, "consumer_cyclical_rsi_max": cc_rsi}
        res = _score_criteria(narrow, crit)
        v31_candidates.append({**res, "label": f"v30c + cc_rsi_max={cc_rsi}"})

    for c in sorted(v31_candidates, key=lambda x: (x["precision"], x["recall"]), reverse=True):
        if c["recall"] >= 0.40:
            delta_p = (c["precision"] - base_p) * 100
            print(
                f"  {c['label']:<55} {c['precision'] * 100:6.1f}%  {c['recall'] * 100:6.1f}%  "
                f"{c['true_positives']:4d}  {c['false_positives']:4d}  {delta_p:+.1f}pp"
            )

    # ── SECTION 6: Missed primes after best v31 gate ─────────────────────────
    print("\n" + "=" * 70)
    print("SECTION 6: Which prime rows get newly missed by CC RSI gate?")
    print("=" * 70)

    # Use cc_rsi_max=50 as representative threshold
    v31_rep = {**V29, "consumer_cyclical_rsi_max": 50}
    newly_missed = [f for f in prime if _apply_criteria(f, V29) and not _apply_criteria(f, v31_rep)]
    if newly_missed:
        print(f"\n  Newly missed by consumer_cyclical_rsi_max=50 ({len(newly_missed)} rows):")
        for f in sorted(newly_missed, key=lambda x: (x["ticker"], x["date"])):
            rsi = f.get("rsi")
            print(f"    {f['date']}  {f['ticker']:<8}  RSI={rsi:.1f}  sector={f.get('sector')}")
    else:
        print("\n  No newly missed primes (all CC TPs have RSI <= 50)")

    print("\n--- Session 13 complete ---")


if __name__ == "__main__":
    main()
