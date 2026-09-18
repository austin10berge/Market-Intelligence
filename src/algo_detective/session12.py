"""Session 12 — PCR (put/call ratio) as a feature on the narrow universe.

Backfill ran 2026-06-20. All 36 dates covered (Sep-Dec 2025), 64-74/74
tickers filled per date.

Questions:
  1. Does pcr_vol discriminate prime days from non-prime days?
  2. What direction? (prime days: more call volume vs put volume, or vice versa?)
  3. What gate threshold improves v29?
  4. Does it work better within specific sectors?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session12
"""

from __future__ import annotations

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria, _score_criteria
from .store import _get_connection, get_all_features

# ── v29 criteria (current best on narrow universe) ────────────────────────────

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


def _join_pcr(features: list[dict]) -> list[dict]:
    """Enrich feature rows with best_iv, pcr_vol, pcr_oi from detective_options."""
    conn = _get_connection()
    try:
        rows = conn.execute(
            "SELECT date, ticker, best_iv, best_volume, occ_symbol, pcr_vol, pcr_oi FROM detective_options"
        ).fetchall()
        index = {(r["date"], r["ticker"]): dict(r) for r in rows}
    finally:
        conn.close()

    enriched = []
    for f in features:
        opt = index.get((f["date"], f["ticker"]), {})
        enriched.append({
            **f,
            "best_iv": opt.get("best_iv"),
            "best_volume": opt.get("best_volume"),
            "occ_symbol": opt.get("occ_symbol"),
            "pcr_vol": opt.get("pcr_vol"),
            "pcr_oi": opt.get("pcr_oi"),
        })
    return enriched


def _ks(a: list[float], b: list[float]) -> tuple[float, float]:
    if len(a) < 5 or len(b) < 5:
        return 0.0, 1.0
    stat, pval = ks_2samp(a, b)
    return round(float(stat), 4), float(pval)


def _pct(vals: list[float], p: int) -> float:
    return round(float(np.percentile(vals, p)), 4)


def main() -> None:
    all_features = get_all_features()

    # ── Narrow universe (prime tickers only) ─────────────────────────────────
    prime_tickers = {f["ticker"] for f in all_features if f["is_prime"] == 1}
    narrow = [f for f in all_features if f["ticker"] in prime_tickers]
    narrow = _join_pcr(narrow)

    prime = [f for f in narrow if f["is_prime"] == 1]
    ctrl  = [f for f in narrow if f["is_prime"] == 0]

    print(f"\n=== Narrow universe: {len(prime)} prime, {len(ctrl)} control ===")

    # ── PCR coverage ─────────────────────────────────────────────────────────
    p_with_pcr = sum(1 for f in prime if f.get("pcr_vol") is not None)
    c_with_pcr = sum(1 for f in ctrl  if f.get("pcr_vol") is not None)
    print(f"pcr_vol coverage: prime {p_with_pcr}/{len(prime)} ({p_with_pcr/len(prime)*100:.1f}%)  "
          f"ctrl {c_with_pcr}/{len(ctrl)} ({c_with_pcr/len(ctrl)*100:.1f}%)")

    # ── KS on full narrow universe ────────────────────────────────────────────
    p_pcr = [f["pcr_vol"] for f in prime if f.get("pcr_vol") is not None]
    c_pcr = [f["pcr_vol"] for f in ctrl  if f.get("pcr_vol") is not None]
    ks, pval = _ks(p_pcr, c_pcr)
    print("\npcr_vol KS (all narrow):")
    print(f"  prime  n={len(p_pcr):4d}  p10={_pct(p_pcr,10):.3f}  median={_pct(p_pcr,50):.3f}  p90={_pct(p_pcr,90):.3f}")
    print(f"  ctrl   n={len(c_pcr):4d}  p10={_pct(c_pcr,10):.3f}  median={_pct(c_pcr,50):.3f}  p90={_pct(c_pcr,90):.3f}")
    print(f"  KS={ks:.4f}  p={pval:.4f}")

    # ── v29 survivors ────────────────────────────────────────────────────────
    v29_survivors = [f for f in narrow if _apply_criteria(f, V29)]
    v29_tp = [f for f in v29_survivors if f["is_prime"] == 1]
    v29_fp = [f for f in v29_survivors if f["is_prime"] == 0]
    print(f"\n=== v29 survivors: {len(v29_tp)} TP, {len(v29_fp)} FP "
          f"(P={len(v29_tp)/(len(v29_tp)+len(v29_fp))*100:.1f}%, "
          f"R={len(v29_tp)/len(prime)*100:.1f}%) ===")

    tp_pcr = [f["pcr_vol"] for f in v29_tp if f.get("pcr_vol") is not None]
    fp_pcr = [f["pcr_vol"] for f in v29_fp if f.get("pcr_vol") is not None]
    ks2, pval2 = _ks(tp_pcr, fp_pcr)
    print("\npcr_vol KS (v29 survivors):")
    print(f"  TP   n={len(tp_pcr):4d}  p10={_pct(tp_pcr,10):.3f}  median={_pct(tp_pcr,50):.3f}  p90={_pct(tp_pcr,90):.3f}")
    print(f"  FP   n={len(fp_pcr):4d}  p10={_pct(fp_pcr,10):.3f}  median={_pct(fp_pcr,50):.3f}  p90={_pct(fp_pcr,90):.3f}")
    print(f"  KS={ks2:.4f}  p={pval2:.4f}")

    # ── pcr_vol gate sweep on top of v29 ─────────────────────────────────────
    print("\n=== pcr_vol gate sweep (on top of v29) ===")
    print(f"  {'Gate':<30} {'P':>7} {'R':>7} {'TP':>5} {'FP':>5}  ΔP    ΔTP")

    base_p = len(v29_tp) / (len(v29_tp) + len(v29_fp))
    base_r = len(v29_tp) / len(prime)
    thresholds_max = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.50, 2.00]
    thresholds_min = [0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.50]

    print("  --- pcr_vol_max ---")
    for thr in thresholds_max:
        crit = {**V29, "pcr_vol_max": thr}
        res = _score_criteria(narrow, crit)
        p = res["precision"]
        r = res["recall"]
        tp = res["true_positives"]
        fp = res["false_positives"]
        delta_p = (p - base_p) * 100
        delta_tp = tp - len(v29_tp)
        print(f"  pcr_vol_max={thr:<5.2f}              {p*100:6.1f}%  {r*100:6.1f}%  {tp:4d}  {fp:4d}  {delta_p:+.1f}pp  {delta_tp:+d}")

    print("  --- pcr_vol_min ---")
    for thr in thresholds_min:
        crit = {**V29, "pcr_vol_min": thr}
        res = _score_criteria(narrow, crit)
        p = res["precision"]
        r = res["recall"]
        tp = res["true_positives"]
        fp = res["false_positives"]
        delta_p = (p - base_p) * 100
        delta_tp = tp - len(v29_tp)
        print(f"  pcr_vol_min={thr:<5.2f}              {p*100:6.1f}%  {r*100:6.1f}%  {tp:4d}  {fp:4d}  {delta_p:+.1f}pp  {delta_tp:+d}")

    # ── Sector breakdown of pcr_vol ───────────────────────────────────────────
    print("\n=== pcr_vol by sector (v29 TPs vs FPs) ===")
    sectors = sorted({f.get("sector") for f in v29_survivors if f.get("sector")}, key=lambda x: x or "")
    for sector in sectors:
        s_tp = [f["pcr_vol"] for f in v29_tp if f.get("sector") == sector and f.get("pcr_vol") is not None]
        s_fp = [f["pcr_vol"] for f in v29_fp if f.get("sector") == sector and f.get("pcr_vol") is not None]
        if len(s_tp) < 3 and len(s_fp) < 3:
            continue
        ks_s, _ = _ks(s_tp, s_fp)
        tp_med = _pct(s_tp, 50) if s_tp else float("nan")
        fp_med = _pct(s_fp, 50) if s_fp else float("nan")
        print(f"  {sector:<30} TP_n={len(s_tp):3d} median={tp_med:.3f}  |  FP_n={len(s_fp):3d} median={fp_med:.3f}  KS={ks_s:.3f}")

    # ── PCR null analysis (which tickers/sectors lack pcr_vol) ───────────────
    no_pcr = [f["ticker"] for f in prime if f.get("pcr_vol") is None]
    if no_pcr:
        from collections import Counter
        print(f"\n=== Prime rows missing pcr_vol ({len(no_pcr)} rows) ===")
        for ticker, cnt in Counter(no_pcr).most_common(15):
            print(f"  {ticker}: {cnt} rows")

    # ── Best combined gate (v30 candidate) ───────────────────────────────────
    print("\n=== v30 candidate search (v29 + best pcr gate) ===")
    best_by_p: list[dict] = []
    for thr in [0.50, 0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.50, 2.00]:
        for direction in ("max", "min"):
            key = f"pcr_vol_{direction}"
            crit = {**V29, key: thr}
            res = _score_criteria(narrow, crit)
            if res["recall"] >= 0.40:
                best_by_p.append({**res, "gate": f"{key}={thr}"})
    best_by_p.sort(key=lambda r: (r["precision"], r["recall"]), reverse=True)
    for r in best_by_p[:5]:
        print(f"  {r['gate']:<28}  P={r['precision']*100:.1f}%  R={r['recall']*100:.1f}%  TP={r['true_positives']}  FP={r['false_positives']}")

    # ── RSI gate test (csp_scanner.py uses max_rsi=50 — test empirically) ───
    print("\n=== RSI gate sweep (v29) ===")
    print("  (csp_scanner.py uses max_rsi=50 — treating as hypothesis, not ground truth)")
    for rsi_max in [50, 52, 54, 55, 57, 60]:
        crit = {**V29, "rsi_max": rsi_max}
        res = _score_criteria(narrow, crit)
        p = res["precision"]
        r = res["recall"]
        delta_p = (p - base_p) * 100
        print(f"  rsi_max={rsi_max}   P={p*100:.1f}%  R={r*100:.1f}%  TP={res['true_positives']}  FP={res['false_positives']}  ΔP={delta_p:+.1f}pp")

    # ── Combined: v29 + best pcr gate + rsi gate ─────────────────────────────
    print("\n=== Combined pcr + rsi sweep (v29 base, recall ≥ 20%) ===")
    for rsi_max in [52, 55, 57, 60, 65]:
        for pcr_dir, pcr_thr in [("max", 0.8), ("max", 1.0), ("max", 1.5), ("max", 2.0)]:
            crit = {**V29, f"pcr_vol_{pcr_dir}": pcr_thr, "rsi_max": rsi_max}
            res = _score_criteria(narrow, crit)
            if res["recall"] >= 0.20:
                p = res["precision"]
                delta_p = (p - base_p) * 100
                print(f"  pcr_vol_{pcr_dir}={pcr_thr} + rsi_max={rsi_max}:  "
                      f"P={p*100:.1f}%  R={res['recall']*100:.1f}%  "
                      f"TP={res['true_positives']}  FP={res['false_positives']}  ΔP={delta_p:+.1f}pp")

    # ── Sector-specific pcr_vol gates ─────────────────────────────────────────
    # Industrials: TPs have HIGHER pcr_vol (0.919 vs 0.805) — try pcr_min for industrials
    # CommSvc: TPs have LOWER pcr_vol (0.445 vs 0.516) — try sector-specific pcr_max
    # Technology: mild (0.447 vs 0.463)
    print("\n=== Sector-specific RSI gate sweep (v29 base) ===")
    # FS: prime RSI=54.1, FP=57.5 (from handoff session10)
    for fs_rsi_max in [52, 54, 56, 58, 60]:
        crit = {**V29, "financials_rsi_max": fs_rsi_max}
        res = _score_criteria(narrow, crit)
        p = res["precision"]
        delta_p = (p - base_p) * 100
        print(f"  financials_rsi_max={fs_rsi_max}:  P={p*100:.1f}%  R={res['recall']*100:.1f}%  "
              f"TP={res['true_positives']}  FP={res['false_positives']}  ΔP={delta_p:+.1f}pp")

    # Tech RSI (inverted vol pattern — what does RSI look like for tech TP vs FP?)
    print()
    tp_rsi_by_sector: dict[str, list[float]] = {}
    fp_rsi_by_sector: dict[str, list[float]] = {}
    for f in v29_tp:
        s = f.get("sector") or "Unknown"
        tp_rsi_by_sector.setdefault(s, []).append(f["rsi"] or 0)
    for f in v29_fp:
        s = f.get("sector") or "Unknown"
        fp_rsi_by_sector.setdefault(s, []).append(f["rsi"] or 0)
    print("  Sector RSI: TP median vs FP median (v29 survivors)")
    for sector in sorted(tp_rsi_by_sector):
        tp_r = tp_rsi_by_sector.get(sector, [])
        fp_r = fp_rsi_by_sector.get(sector, [])
        if len(tp_r) < 3 or len(fp_r) < 3:
            continue
        ks_r, _ = _ks(tp_r, fp_r)
        print(f"  {sector:<30} TP_med={_pct(tp_r,50):.1f}  FP_med={_pct(fp_r,50):.1f}  KS={ks_r:.3f}")


if __name__ == "__main__":
    main()
