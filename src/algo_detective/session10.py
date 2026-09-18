"""Session 10 — Narrow universe: push past v28, Financial Services FP analysis.

v28 achieves P=37.1%, R=54.4% (TP=153, FP=259) on the 74-ticker narrow universe.
Remaining FPs: 109 Financial Services (MS, BAC, WFC, AXP), 83 Tech (ADI, MSFT, IBM).
Remaining FNs: 128 missed primes — UAL(11), ANET(8), NVDA(7), etc.

This session:
  1. Deep-dive on Financial Services FPs — what distinguishes them from FS TPs?
  2. Test sector-specific FS gates within v28 (volume_ratio, adx, iv gates)
  3. Technology FP analysis within v28 survivors
  4. Explore whether loosening h52 to 14-15 + adding vr recovers TPs without losing precision
  5. Grid search for v29 — target P≥38% at R≥58%

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session10
"""

from __future__ import annotations

import logging
import warnings
from collections import Counter

import numpy as np
from scipy.stats import ks_2samp

from .analyze import _apply_criteria
from .store import get_all_features, get_options_index

warnings.filterwarnings("ignore", category=FutureWarning)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)

V26 = {
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
    "iv_rv_min": 0.9,
}

V28 = {
    **V26,
    "bb_width_pct_max": 14.0,
    "volume_ratio_max": 1.10,
    "pct_from_52wk_high_max": 12,
}


def _load_narrow() -> list[dict]:
    features = get_all_features()
    prime_tickers = {f["ticker"] for f in features if f["is_prime"] == 1}
    narrow = [f for f in features if f["ticker"] in prime_tickers]
    options_idx = get_options_index()
    return [
        {**f, "best_iv": options_idx.get((f["date"], f["ticker"]), {}).get("best_iv")}
        for f in narrow
    ]


def _score(rows: list[dict], criteria: dict) -> dict:
    prime = [r for r in rows if r["is_prime"] == 1]
    ctrl  = [r for r in rows if r["is_prime"] == 0]
    tp = sum(1 for r in prime if _apply_criteria(r, criteria))
    fp = sum(1 for r in ctrl  if _apply_criteria(r, criteria))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec  = tp / len(prime) if prime else 0.0
    return {"tp": tp, "fp": fp, "precision": round(prec, 4), "recall": round(rec, 4)}


def _ks_profile(rows_a: list[dict], rows_b: list[dict], feats: list[str]) -> list[tuple]:
    results = []
    for feat in feats:
        a_vals = [r.get(feat) for r in rows_a if r.get(feat) is not None]
        b_vals = [r.get(feat) for r in rows_b if r.get(feat) is not None]
        if len(a_vals) < 5 or len(b_vals) < 5:
            continue
        ks, _ = ks_2samp(a_vals, b_vals)
        results.append((feat, ks, float(np.median(a_vals)), float(np.median(b_vals))))
    return sorted(results, key=lambda x: x[1], reverse=True)


FEATS = ["rv20", "bb_width_pct", "volume_ratio", "adx", "rsi", "macd_histogram",
         "roc20", "pct_from_52wk_high", "atr_pct", "adr20_pct", "best_iv",
         "market_cap_b", "forward_pe", "peg_ratio", "beta", "dividend_yield"]


def main():
    print("\n=== Session 10 — Sector FP Deep-dive + v29 Search ===\n")

    rows = _load_narrow()
    prime = [r for r in rows if r["is_prime"] == 1]
    ctrl  = [r for r in rows if r["is_prime"] == 0]

    s28 = _score(rows, V28)
    print(f"v28 baseline: P={s28['precision']:.1%}  R={s28['recall']:.1%}  TP={s28['tp']}  FP={s28['fp']}\n")

    v28_tps = [r for r in prime if _apply_criteria(r, V28)]
    v28_fps = [r for r in ctrl  if _apply_criteria(r, V28)]
    v28_fns = [r for r in prime if not _apply_criteria(r, V28)]

    # --- Part 1: Financial Services FP analysis ---
    print("--- Part 1: Financial Services FP deep-dive ---")

    fs_fps = [r for r in v28_fps if r.get("sector") == "Financial Services"]
    fs_tps = [r for r in v28_tps if r.get("sector") == "Financial Services"]
    print(f"  FS TPs: {len(fs_tps)}  |  FS FPs: {len(fs_fps)}")

    print("\n  Top FS FP tickers:")
    for ticker, cnt in Counter(r["ticker"] for r in fs_fps).most_common(15):
        print(f"    {ticker:<8} {cnt:>3}")

    print("\n  FS TPs tickers:")
    for ticker, cnt in Counter(r["ticker"] for r in fs_tps).most_common():
        print(f"    {ticker:<8} {cnt:>3}")

    if fs_fps and fs_tps:
        print("\n  FS feature comparison (TPs vs FPs):")
        results = _ks_profile(fs_tps, fs_fps, FEATS)
        print(f"  {'Feature':<28} {'KS':>6}  {'TP median':>10}  {'FP median':>10}  Direction")
        for feat, ks, tp_med, fp_med in results:
            direction = "TP<FP" if tp_med < fp_med else "TP>FP"
            print(f"  {feat:<28} {ks:>6.3f}  {tp_med:>10.3f}  {fp_med:>10.3f}  {direction}")

    # Test FS-specific gates (added on top of V28)
    print("\n  FS-specific gate candidates (on top of v28):")
    print(f"  {'Gate':<40} {'Value':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}")
    base_p = s28["precision"]

    for gate_name, values in [
        ("financials_volume_ratio_max", [0.80, 0.85, 0.90, 0.95, 1.00]),
        ("financials_adx_min", [18, 20, 22, 25]),
        ("financials_market_cap_b_min", [150, 200, 250, 300]),
    ]:
        for val in values:
            criteria = {**V28, gate_name: val}
            s = _score(rows, criteria)
            delta_p = s["precision"] - base_p
            sign = "+" if delta_p >= 0 else ""
            print(f"  {gate_name:<40} {val:>7}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}  {sign}{delta_p:.1%}")

    # --- Part 2: Technology FP analysis within v28 ---
    print("\n--- Part 2: Technology FP deep-dive ---")

    tech_fps = [r for r in v28_fps if r.get("sector") == "Technology"]
    tech_tps = [r for r in v28_tps if r.get("sector") == "Technology"]
    print(f"  Tech TPs: {len(tech_tps)}  |  Tech FPs: {len(tech_fps)}")

    print("\n  Top Tech FP tickers:")
    for ticker, cnt in Counter(r["ticker"] for r in tech_fps).most_common(10):
        print(f"    {ticker:<8} {cnt:>3}")

    print("\n  Tech TPs tickers:")
    for ticker, cnt in Counter(r["ticker"] for r in tech_tps).most_common():
        print(f"    {ticker:<8} {cnt:>3}")

    if tech_fps and tech_tps:
        print("\n  Tech feature comparison (TPs vs FPs):")
        results = _ks_profile(tech_tps, tech_fps, FEATS)
        print(f"  {'Feature':<28} {'KS':>6}  {'TP median':>10}  {'FP median':>10}  Direction")
        for feat, ks, tp_med, fp_med in results:
            direction = "TP<FP" if tp_med < fp_med else "TP>FP"
            print(f"  {feat:<28} {ks:>6.3f}  {tp_med:>10.3f}  {fp_med:>10.3f}  {direction}")

    # Tech-specific gates
    print("\n  Tech-specific gate candidates:")
    for gate_name, values in [
        ("technology_volume_ratio_max", [0.80, 0.85, 0.90, 0.95, 1.00]),
        ("technology_market_cap_b_min", [150, 200, 250]),
    ]:
        for val in values:
            criteria = {**V28, gate_name: val}
            s = _score(rows, criteria)
            delta_p = s["precision"] - base_p
            sign = "+" if delta_p >= 0 else ""
            print(f"  {gate_name:<40} {val:>7}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}  {sign}{delta_p:.1%}")

    # --- Part 3: Recall recovery — can we loosen h52 and compensate? ---
    print("\n--- Part 3: Recall recovery experiments ---")
    print(f"  v28 FNs: {len(v28_fns)}. Testing h52 relaxation + volume_ratio tightening.")

    print("\n  h52 relax + vr tighten (target: P≥37%, R≥58%):")
    print(f"  {'h52_max':>8} {'vr_max':>8}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for h52 in [12, 13, 14, 15, 16, 18]:
        for vr in [0.95, 1.00, 1.05, 1.10]:
            criteria = {**V26, "bb_width_pct_max": 14.0, "pct_from_52wk_high_max": h52, "volume_ratio_max": vr}
            s = _score(rows, criteria)
            marker = " ←" if s["precision"] >= 0.37 and s["recall"] >= 0.58 else ""
            print(f"  {h52:>8} {vr:>8.2f}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}{marker}")

    # --- Part 4: v29 grid search (volume_ratio + adx_min + sector gates) ---
    print("\n--- Part 4: v29 grid search ---")

    best_v29 = None
    all_v29 = []

    # Layer on v28 base: try adx tightening, sector gates, macd gate
    for vr in [1.00, 1.05, 1.10]:
        for h52 in [12, 14, 15]:
            for adx_min in [15, 17, 18]:
                for fs_vr_max in [None, 0.90, 0.95, 1.00]:
                    criteria = {**V26, "bb_width_pct_max": 14.0, "pct_from_52wk_high_max": h52,
                                "volume_ratio_max": vr, "adx_min": adx_min}
                    if fs_vr_max is not None:
                        criteria["financials_volume_ratio_max"] = fs_vr_max
                    s = _score(rows, criteria)
                    all_v29.append({**s, "vr": vr, "h52": h52, "adx": adx_min, "fs_vr": fs_vr_max})

    # Also test: v28 + macd on a few values
    for macd in [0.0, 0.3, 0.5]:
        criteria = {**V28, "macd_histogram_max": macd}
        s = _score(rows, criteria)
        all_v29.append({**s, "vr": 1.10, "h52": 12, "adx": 15, "fs_vr": None, "macd": macd})

    # Sort by precision, filter recall>=50%
    all_v29_filtered = [c for c in all_v29 if c["recall"] >= 0.50]
    all_v29_filtered.sort(key=lambda x: x["precision"], reverse=True)

    print("\n  P≥35%, R≥50% candidates:")
    print(f"  {'h52':>5} {'vr':>6} {'adx':>5} {'fs_vr':>8}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    seen_key = set()
    printed = 0
    for c in all_v29_filtered:
        if c.get("precision", 0) < 0.35:
            continue
        key = (c["h52"], c["vr"], c.get("adx", 15), c.get("fs_vr"))
        if key in seen_key:
            continue
        seen_key.add(key)
        fs_vr_str = f"{c['fs_vr']:.2f}" if c.get("fs_vr") is not None else "  —  "
        macd_str = f"macd≤{c['macd']:.1f}" if c.get("macd") is not None else ""
        print(f"  {c['h52']:>5} {c['vr']:>6.2f} {c.get('adx',15):>5} {fs_vr_str:>8}  {c['precision']:>6.1%}  {c['recall']:>6.1%}  {c['tp']:>5}  {c['fp']:>5}  {macd_str}")
        printed += 1
        if printed >= 20:
            break

    # Pareto for v29 candidates
    print("\n  Pareto (best P at each recall tier, from v29 grid):")
    for tier in [0.50, 0.55, 0.58, 0.60, 0.65]:
        tier_cands = [c for c in all_v29 if c["recall"] >= tier]
        if not tier_cands:
            continue
        best = max(tier_cands, key=lambda x: x["precision"])
        h52 = f"h52≤{best['h52']}"
        vr   = f"vr≤{best['vr']:.2f}"
        adx  = f"adx≥{best.get('adx',15)}" if best.get("adx", 15) > 15 else ""
        fs_vr = f"fs_vr≤{best['fs_vr']:.2f}" if best.get("fs_vr") else ""
        gates = " + ".join(g for g in [h52, vr, adx, fs_vr] if g)
        print(f"  R≥{tier:.0%}: P={best['precision']:.1%}  TP={best['tp']}  FP={best['fp']}  |  bb≤14 + {gates}")

    # --- Summary ---
    print("\n--- Summary ---")
    print(f"  v28:  P={s28['precision']:.1%}  R={s28['recall']:.1%}  TP={s28['tp']}  FP={s28['fp']}")
    if all_v29_filtered:
        best = all_v29_filtered[0]
        fs_vr_str = f"financials_volume_ratio_max={best['fs_vr']:.2f}" if best.get("fs_vr") else ""
        print(f"  v29 best at R≥50%: P={best['precision']:.1%}  R={best['recall']:.1%}  TP={best['tp']}  FP={best['fp']}")
        print(f"    Gates: bb_width_pct_max=14 + pct_from_52wk_high_max={best['h52']} + volume_ratio_max={best['vr']:.2f}"
              + (f" + adx_min={best.get('adx',15)}" if best.get("adx", 15) > 15 else "")
              + (f" + {fs_vr_str}" if fs_vr_str else ""))


if __name__ == "__main__":
    main()
