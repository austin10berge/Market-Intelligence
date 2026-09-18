"""Session 09 — Narrow universe: FN/FP profiling + v28 search.

v27 achieves P=34.0%, R=52.3% (TP=147, FP=286) on the 74-ticker narrow universe.
Recall dropped 17pp vs v26 baseline (69.8%). This session answers:

  1. Which gate(s) kill most missed primes (false negatives)?
  2. Who are the 286 FPs — are they specific tickers or spread?
  3. Can we find v28 with P≥30% at R≥65%?
  4. Does splitting gates by sector or ticker improve anything?

Run:
  docker compose run --rm pipeline python -m src.algo_detective.session09
"""

from __future__ import annotations

import logging
import warnings
from collections import Counter, defaultdict

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

V27 = {
    **V26,
    "bb_width_pct_max": 14.0,
    "macd_histogram_max": 0.5,
    "pct_from_52wk_high_max": 12,  # tightened from 18
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
    fn = len(prime) - tp
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec  = tp / len(prime) if prime else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(prec, 4), "recall": round(rec, 4)}


def _which_gate_blocks(row: dict, criteria: dict) -> list[str]:
    """Return list of gate keys that individually block this row."""
    blocked = []
    for key, val in criteria.items():
        single = {key: val}
        if not _apply_criteria(row, single):
            blocked.append(key)
    return blocked


def main():
    print("\n=== Session 09 — FN/FP Profiling + v28 Search ===\n")

    rows = _load_narrow()
    prime = [r for r in rows if r["is_prime"] == 1]
    ctrl  = [r for r in rows if r["is_prime"] == 0]

    s26 = _score(rows, V26)
    s27 = _score(rows, V27)
    print(f"v26 on narrow: P={s26['precision']:.1%}  R={s26['recall']:.1%}  TP={s26['tp']}  FP={s26['fp']}")
    print(f"v27 on narrow: P={s27['precision']:.1%}  R={s27['recall']:.1%}  TP={s27['tp']}  FP={s27['fp']}")

    # --- Part 1: Which gate(s) block missed primes? ---
    print("\n--- Part 1: Gate attribution for v27 false negatives ---")

    # Primes that pass v26 but fail v27 (the incremental FNs)
    v26_tps  = [r for r in prime if _apply_criteria(r, V26)]
    v27_tps  = [r for r in prime if _apply_criteria(r, V27)]
    v27_fns  = [r for r in prime if not _apply_criteria(r, V27)]

    v27_new_gates = {k: v for k, v in V27.items() if k not in V26 or V27[k] != V26.get(k)}
    print(f"\nv27 new/tightened gates: {list(v27_new_gates.keys())}")
    print(f"v26 TPs: {len(v26_tps)}  |  v27 TPs: {len(v27_tps)}  |  v27 FNs: {len(v27_fns)}")

    # For each FN, find which v27 gates block it
    gate_blame: Counter = Counter()
    exclusive_blame: Counter = Counter()
    for r in v27_fns:
        blockers = _which_gate_blocks(r, v27_new_gates)
        for b in blockers:
            gate_blame[b] += 1
        if len(blockers) == 1:
            exclusive_blame[blockers[0]] += 1

    print("\n  Gate blocking FNs (multiple gates can block same row):")
    for gate, cnt in gate_blame.most_common():
        print(f"    {gate:<35} blocks {cnt:>3} FNs")

    print("\n  Exclusive blame (gate is the ONLY reason row fails):")
    for gate, cnt in exclusive_blame.most_common():
        print(f"    {gate:<35} solely responsible for {cnt:>3} FNs")

    # What values do the blocked primes have for the new gate features?
    print("\n  FN distributions for new gate features:")
    for feat in ["bb_width_pct", "macd_histogram", "pct_from_52wk_high"]:
        fn_vals = [r.get(feat) for r in v27_fns if r.get(feat) is not None]
        tp_vals = [r.get(feat) for r in v27_tps if r.get(feat) is not None]
        if fn_vals and tp_vals:
            print(f"  {feat}:")
            print(f"    FN: p10={np.percentile(fn_vals,10):.2f}  median={np.median(fn_vals):.2f}  p90={np.percentile(fn_vals,90):.2f}  (n={len(fn_vals)})")
            print(f"    TP: p10={np.percentile(tp_vals,10):.2f}  median={np.median(tp_vals):.2f}  p90={np.percentile(tp_vals,90):.2f}  (n={len(tp_vals)})")

    # Top FN tickers
    fn_tickers = Counter(r["ticker"] for r in v27_fns)
    print("\n  Top tickers in v27 FNs:")
    for ticker, cnt in fn_tickers.most_common(15):
        print(f"    {ticker:<8} {cnt:>3} missed primes")

    # --- Part 2: FP profile ---
    print("\n--- Part 2: v27 false positive profile ---")
    v27_fps = [r for r in ctrl if _apply_criteria(r, V27)]
    fp_tickers = Counter(r["ticker"] for r in v27_fps)
    fp_sectors = Counter(r.get("sector", "Unknown") for r in v27_fps)

    print("\n  FP by sector:")
    for sector, cnt in fp_sectors.most_common():
        pct = cnt / len(v27_fps) * 100
        bar = "█" * int(pct / 2)
        print(f"  {sector:<30} {cnt:>4}  ({pct:.1f}%)  {bar}")

    print("\n  Top FP tickers:")
    for ticker, cnt in fp_tickers.most_common(20):
        sector = next((r.get("sector", "?") for r in v27_fps if r["ticker"] == ticker), "?")
        print(f"    {ticker:<8} {cnt:>3}  [{sector}]")

    # FP vs TP feature comparison (within v27 survivors)
    print("\n  FP vs TP feature comparison (among v27 passes):")
    v27_all_passes = [r for r in rows if _apply_criteria(r, V27)]
    v27_pass_tps = [r for r in v27_all_passes if r["is_prime"] == 1]
    v27_pass_fps = [r for r in v27_all_passes if r["is_prime"] == 0]

    feats_to_compare = ["rv20", "macd_histogram", "bb_width_pct", "adx", "rsi",
                        "volume_ratio", "roc20", "pct_from_52wk_high", "adr20_pct",
                        "best_iv", "market_cap_b"]
    ks_results = []
    for feat in feats_to_compare:
        tp_v = [r.get(feat) for r in v27_pass_tps if r.get(feat) is not None]
        fp_v = [r.get(feat) for r in v27_pass_fps if r.get(feat) is not None]
        if len(tp_v) < 5 or len(fp_v) < 5:
            continue
        ks, _ = ks_2samp(tp_v, fp_v)
        ks_results.append((feat, ks, float(np.median(tp_v)), float(np.median(fp_v))))
    ks_results.sort(key=lambda x: x[1], reverse=True)
    print(f"  {'Feature':<28} {'KS':>6}  {'TP median':>10}  {'FP median':>10}  Direction")
    for feat, ks, tp_med, fp_med in ks_results:
        direction = "TP<FP" if tp_med < fp_med else "TP>FP"
        print(f"  {feat:<28} {ks:>6.3f}  {tp_med:>10.3f}  {fp_med:>10.3f}  {direction}")

    # --- Part 3: v28 search — target P≥30% at R≥65% ---
    print("\n--- Part 3: v28 search — target P≥30% at R≥65% ---")

    # Strategy: v26 base + tighter gates that preserve recall better than v27
    # v27 kills recall mainly via pct_from_52wk_high_max=12 and macd_histogram_max=0.5
    # Try: looser macd threshold, keep h52 tight, add a different soft gate

    candidates = []

    # Grid: bb_width, macd (wider), h52, rv20, rsi, volume_ratio
    bb_vals    = [13.0, 14.0, 15.0, 16.0]
    macd_vals  = [0.0, 0.5, 1.0, 1.5, None]
    h52_vals   = [12, 14, 15, 16, 18]
    rv_vals    = [0.30, 0.33, 0.35, 0.38, 0.40, None]
    rsi_vals   = [55, 58, 60, 62, 65, None]

    for bb in bb_vals:
        for macd in macd_vals:
            for h52 in h52_vals:
                criteria = {**V26, "bb_width_pct_max": bb, "pct_from_52wk_high_max": h52}
                if macd is not None:
                    criteria["macd_histogram_max"] = macd
                s = _score(rows, criteria)
                candidates.append({**s, "bb": bb, "macd": macd, "h52": h52, "rv": None, "rsi": None})

    for rv in [v for v in rv_vals if v is not None]:
        for macd in [0.5, 1.0, 1.5, None]:
            for h52 in [14, 15, 16, 18]:
                criteria = {**V26, "rv20_max": rv, "pct_from_52wk_high_max": h52}
                if macd is not None:
                    criteria["macd_histogram_max"] = macd
                s = _score(rows, criteria)
                candidates.append({**s, "bb": None, "macd": macd, "h52": h52, "rv": rv, "rsi": None})

    # bb + rv combos without macd
    for bb in [13.0, 14.0, 15.0]:
        for rv in [0.30, 0.33, 0.35]:
            for h52 in [14, 15, 16]:
                criteria = {**V26, "bb_width_pct_max": bb, "rv20_max": rv, "pct_from_52wk_high_max": h52}
                s = _score(rows, criteria)
                candidates.append({**s, "bb": bb, "macd": None, "h52": h52, "rv": rv, "rsi": None})

    # volume_ratio_max sweep — KS=0.212 within v27 passes (TPs: 0.837, FPs: 0.942)
    print("\n  volume_ratio_max sweep (on top of v26):")
    print(f"  {'vr_max':>8}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}  {'ΔP':>7}")
    for vr in [0.80, 0.85, 0.90, 0.95, 1.00, 1.05, 1.10, 1.20]:
        s = _score(rows, {**V26, "volume_ratio_max": vr})
        delta_p = s["precision"] - s26["precision"]
        sign = "+" if delta_p >= 0 else ""
        print(f"  {vr:>8.2f}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}  {sign}{delta_p:.1%}")

    # adx_min tightening sweep
    print("\n  adx_min tightening sweep (TPs median=22.2 vs FPs=20.1):")
    print(f"  {'adx_min':>8}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for adx_min in [15, 17, 18, 19, 20, 22, 25]:
        s = _score(rows, {**V26, "adx_min": adx_min})
        print(f"  {adx_min:>8}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}")

    # Best bb+h52 + volume_ratio combos
    print("\n  bb_width_pct_max=14 + pct_52wk_max=12 + volume_ratio_max sweep:")
    print(f"  {'vr_max':>8}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for vr in [0.85, 0.90, 0.95, 1.00, 1.05, 1.10]:
        s = _score(rows, {**V26, "bb_width_pct_max": 14.0, "pct_from_52wk_high_max": 12, "volume_ratio_max": vr})
        print(f"  {vr:>8.2f}  {s['precision']:>6.1%}  {s['recall']:>6.1%}  {s['tp']:>5}  {s['fp']:>5}")

    # Filter to P≥28%, R≥60%
    filtered = [c for c in candidates if c["precision"] >= 0.28 and c["recall"] >= 0.60]
    filtered.sort(key=lambda x: (x["precision"], x["recall"]), reverse=True)

    print("\n  P≥28%, R≥60% candidates (top 25):")
    print(f"  {'bb_max':>7} {'macd_max':>9} {'h52_max':>8} {'rv_max':>7}  {'P':>7}  {'R':>7}  {'TP':>5}  {'FP':>5}")
    for c in filtered[:25]:
        bb   = f"{c['bb']:.1f}" if c["bb"] is not None else "  —  "
        macd = f"{c['macd']:.1f}" if c["macd"] is not None else "  —  "
        h52  = str(c["h52"])
        rv   = f"{c['rv']:.2f}" if c["rv"] is not None else "  —  "
        print(f"  {bb:>7} {macd:>9} {h52:>8} {rv:>7}  {c['precision']:>6.1%}  {c['recall']:>6.1%}  {c['tp']:>5}  {c['fp']:>5}")

    # Also print pareto frontier: for each recall bucket, best precision
    print("\n  Pareto: best precision at each recall tier (from all candidates):")
    all_filtered = [c for c in candidates if c["recall"] >= 0.50]
    recall_tiers = [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]
    for tier in recall_tiers:
        tier_cands = [c for c in all_filtered if c["recall"] >= tier]
        if not tier_cands:
            continue
        best = max(tier_cands, key=lambda x: x["precision"])
        bb   = f"bb≤{best['bb']:.0f}" if best["bb"] is not None else ""
        macd = f"macd≤{best['macd']:.1f}" if best["macd"] is not None else ""
        h52  = f"h52≤{best['h52']}"
        rv   = f"rv≤{best['rv']:.2f}" if best["rv"] is not None else ""
        gates = " + ".join(g for g in [bb, macd, h52, rv] if g)
        print(f"  R≥{tier:.0%}: best P={best['precision']:.1%}  TP={best['tp']}  FP={best['fp']}  |  {gates}")

    # --- Part 4: Can sector-specific gates recover FNs? ---
    print("\n--- Part 4: Sector breakdown of FNs and FPs ---")

    fn_sectors = Counter(r.get("sector", "Unknown") for r in v27_fns)
    tp_sectors = Counter(r.get("sector", "Unknown") for r in v27_tps)

    print(f"\n  {'Sector':<30} {'FNs':>5}  {'TPs':>5}  {'FN rate':>8}")
    all_sectors = sorted(set(list(fn_sectors.keys()) + list(tp_sectors.keys())), key=lambda x: x or "")
    for sector in all_sectors:
        fn_cnt = fn_sectors.get(sector, 0)
        tp_cnt = tp_sectors.get(sector, 0)
        total  = fn_cnt + tp_cnt
        fn_rate = fn_cnt / total if total else 0.0
        print(f"  {sector:<30} {fn_cnt:>5}  {tp_cnt:>5}  {fn_rate:>7.1%}")

    # For the biggest FN sector, test relaxing the blocking gate
    print("\n  FN breakdown by blocking gate, per sector:")
    sector_gate_blame: dict[str, Counter] = defaultdict(Counter)
    for r in v27_fns:
        sector = r.get("sector", "Unknown")
        blockers = _which_gate_blocks(r, v27_new_gates)
        for b in blockers:
            sector_gate_blame[sector][b] += 1

    for sector, blame in sorted(sector_gate_blame.items(), key=lambda x: sum(x[1].values()), reverse=True):
        print(f"  {sector}:")
        for gate, cnt in blame.most_common():
            print(f"    {gate:<35} {cnt:>3}")

    # --- Summary ---
    print("\n--- Summary ---")
    print(f"  v26 on narrow:  P={s26['precision']:.1%}  R={s26['recall']:.1%}  TP={s26['tp']}  FP={s26['fp']}")
    print(f"  v27 on narrow:  P={s27['precision']:.1%}  R={s27['recall']:.1%}  TP={s27['tp']}  FP={s27['fp']}")
    if filtered:
        best_v28 = filtered[0]
        print(f"  v28 candidate:  P={best_v28['precision']:.1%}  R={best_v28['recall']:.1%}  TP={best_v28['tp']}  FP={best_v28['fp']}")
        bb   = f"bb_width_pct_max={best_v28['bb']:.1f}" if best_v28["bb"] is not None else ""
        macd = f"macd_histogram_max={best_v28['macd']:.1f}" if best_v28["macd"] is not None else ""
        h52  = f"pct_from_52wk_high_max={best_v28['h52']}"
        rv   = f"rv20_max={best_v28['rv']:.2f}" if best_v28["rv"] is not None else ""
        gates = " + ".join(g for g in [bb, macd, h52, rv] if g)
        print(f"  v28 gates (on top of v26): {gates}")


if __name__ == "__main__":
    main()
